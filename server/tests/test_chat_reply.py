from datetime import datetime, timedelta, timezone
from pathlib import Path

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

    store.finish_planning(planning.id, outcome="reply", lane="fast", delay_seconds=0)
    typing = store.claim_due()
    assert typing is not None
    assert typing.status == "typing"
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
    assert ChatReplyPlanner.deterministic_plan(
        [{"role": "user", "content": "好的"}],
        allow_silent=True,
    ).outcome == "silent"
    assert ChatReplyPlanner.deterministic_plan(
        [{"role": "user", "content": "好了吗？"}],
        allow_silent=True,
    ).outcome == "reply"
    assert ChatReplyPlanner.deterministic_plan(
        [{"role": "user", "content": "好的", "onboarding_reply": True}],
        allow_silent=False,
    ).outcome == "reply"
