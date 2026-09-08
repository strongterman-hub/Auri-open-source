from datetime import datetime, timedelta, timezone
import asyncio
import sqlite3
from pathlib import Path

from app.agent.llm import LLMResponse
from app.chat.reply_planner import ChatReplyPlanner
from app.chat.reply_store import ChatReplyStore


def test_reply_store_batches_and_exposes_typing_only_during_generation(
    tmp_dir: Path,
) -> None:
    store = ChatReplyStore(tmp_dir / "chat" / "replies.db")
    first = store.enqueue(
        session_id="session-1",
        user_id="user-1",
        agent_id="default",
        message_id="message-1",
        debounce_seconds=0,
    )
    second = store.enqueue(
        session_id="session-1",
        user_id="user-1",
        agent_id="default",
        message_id="message-2",
        debounce_seconds=0,
    )

    assert second.id == first.id
    assert second.message_ids == ["message-1", "message-2"]
    assert store.reply_state("session-1") == "queued"

    planning = store.claim_due()
    assert planning is not None
    assert planning.status == "planning"
    assert store.reply_state("session-1") == "queued"

    store.finish_planning(
        planning.id,
        outcome="reply",
        lane="fast",
        verbosity="short",
        delay_seconds=0,
    )
    typing = store.claim_due()
    assert typing is not None
    assert typing.status == "typing"
    assert typing.verbosity == "short"
    assert store.reply_state("session-1") == "typing"

    store.complete(typing.id)
    assert store.reply_state("session-1") == "idle"


def test_expired_planning_lease_is_replanned(tmp_dir: Path) -> None:
    store = ChatReplyStore(tmp_dir / "replies.db")
    job = store.enqueue(
        session_id="session-1",
        user_id="user-1",
        agent_id="default",
        message_id="message-1",
        debounce_seconds=0,
    )
    planning = store.claim_due(lease_seconds=30)
    assert planning is not None

    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with store._connect() as connection:
        connection.execute(
            "UPDATE chat_reply_jobs SET lease_until = ? WHERE id = ?",
            (expired, job.id),
        )

    reclaimed = store.claim_due()
    assert reclaimed is not None
    assert reclaimed.status == "planning"
    assert reclaimed.planned is False


def test_deterministic_silence_never_swallows_questions_or_onboarding() -> None:
    closure = ChatReplyPlanner.deterministic_plan(
        [{"role": "user", "content": "好的"}],
        allow_silent=True,
    )
    assert closure.outcome == "silent"
    assert closure.verbosity == "micro"
    question = ChatReplyPlanner.deterministic_plan(
        [{"role": "user", "content": "好了吗？"}],
        allow_silent=True,
    )
    assert question.outcome == "reply"
    assert question.verbosity == "short"
    onboarding = ChatReplyPlanner.deterministic_plan(
        [{"role": "user", "content": "好的", "onboarding_reply": True}],
        allow_silent=False,
    )
    assert onboarding.outcome == "reply"
    assert onboarding.verbosity == "micro"


class _PlannerLLM:
    async def complete(self, messages, tools=None, max_tokens=None, model=None):
        return LLMResponse(
            content=(
                '{"outcome":"reply","lane":"normal",'
                '"verbosity":"detailed","reason":"explicit depth"}'
            )
        )


def test_model_planner_returns_verbosity_band() -> None:
    planner = ChatReplyPlanner(_PlannerLLM())
    plan = asyncio.run(
        planner.plan(
            [{"role": "user", "content": "我们聊聊这个设计"}],
            allow_silent=True,
        )
    )

    assert plan.lane == "normal"
    assert plan.verbosity == "detailed"


class _InvalidVerbosityLLM:
    async def complete(self, messages, tools=None, max_tokens=None, model=None):
        return LLMResponse(
            content=(
                '{"outcome":"reply","lane":"normal",'
                '"verbosity":"huge","reason":"bad band"}'
            )
        )


def test_invalid_model_verbosity_falls_back_to_local_policy() -> None:
    planner = ChatReplyPlanner(_InvalidVerbosityLLM())
    plan = asyncio.run(
        planner.plan(
            [{"role": "user", "content": "我们聊聊这个设计"}],
            allow_silent=True,
        )
    )

    assert plan.verbosity == "short"


def test_reply_store_migrates_existing_database_without_verbosity(tmp_dir: Path) -> None:
    path = tmp_dir / "legacy-replies.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE chat_reply_jobs (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT 'default',
                status TEXT NOT NULL,
                message_ids TEXT NOT NULL,
                planned INTEGER NOT NULL DEFAULT 0,
                lane TEXT,
                first_received_at TEXT NOT NULL,
                last_received_at TEXT NOT NULL,
                not_before TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                lease_until TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    store = ChatReplyStore(path)
    with store._connect() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(chat_reply_jobs)")
        }

    assert "verbosity" in columns
