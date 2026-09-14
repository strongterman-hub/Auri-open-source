import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.agent.llm import LLMClient, LLMResponse, OpenAICompatibleClient
from app.agent.runner import AgentRunContext, BasicAgentRunner
from app.agent.situation import is_correction
from app.agent.structured import structured_json
from app.agent.tools import Tool
from app.health.evidence import sleep_evidence
from app.memory.event_extraction import EventExtractor
from app.memory.event_jobs import EventJobStore
from app.memory.events import EventCandidate, EventMemoryStore, EventStatus
from app.memory.models import MemoryScope, OriginClass
from app.observation.store import ObservationStore
from app.services.event_memory_service import EventMemoryService
from app.schedule.scheduler import ScheduleScheduler

UTC = timezone.utc


class Sequence(LLMClient):
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, messages, tools=None, max_tokens=None, model=None):
        self.calls.append(messages)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return (
            value
            if isinstance(value, LLMResponse)
            else LLMResponse(content=json.dumps(value, ensure_ascii=False))
        )


def msg(text, role="user", mid="m", at=None):
    return {
        "id": mid,
        "role": role,
        "content": text,
        "timestamp": (at or datetime.now(UTC)).isoformat(),
    }


def context(text, tools=None):
    return AgentRunContext(
        session_id="s",
        scope=MemoryScope(user_id="u"),
        history=[msg(text)],
        memory_prompt="班会在2026-09-14上午。",
        current_time="2026-09-14T20:35:00+08:00",
        tools=tools or [],
    )


def test_structured_retries_empty_and_rejects_truncated_valid_json():
    llm = Sequence(
        LLMResponse(content="{}", finish_reason="length"), {"verdict": "safe"}
    )
    assert asyncio.run(structured_json(llm, [])) == {"verdict": "safe"}
    assert len(llm.calls) == 2
    llm = Sequence(LLMResponse(content=""), LLMResponse(content=""))
    with pytest.raises(ValueError):
        asyncio.run(structured_json(llm, []))
    assert len(llm.calls) == 2


def test_deepseek_structured_toggle_and_finish_diagnostics(monkeypatch):
    sent = []

    async def post(_self, url, **kwargs):
        sent.append(kwargs["json"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "choices": [{"message": {"content": "[]"}, "finish_reason": "stop"}],
                "usage": {
                    "completion_tokens": 10,
                    "completion_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    client = OpenAICompatibleClient("https://provider.invalid", "deepseek-v4-flash")
    answer = asyncio.run(client.complete_structured([], max_tokens=512))
    assert sent[-1]["thinking"] == {"type": "disabled"}
    assert answer.finish_reason == "stop"
    asyncio.run(client.complete([]))
    assert "thinking" not in sent[-1]
    client.model = "other-provider-model"
    asyncio.run(client.complete_structured([]))
    assert "thinking" not in sent[-1]


def test_jobs_restart_priority_lease_and_delete(tmp_path):
    path = tmp_path / "m.db"
    store = EventJobStore(path)
    store.enqueue("u", "default", "s", "normal")
    store.enqueue("u", "default", "s", "correction", 1)
    job = EventJobStore(path).claim(now=100)
    assert job["message_id"] == "correction"
    store.enqueue("u", "default", "s", "correction", 1)
    assert EventJobStore(path).claim(now=401)["message_id"] == "correction"
    store.delete_user("u", "default")
    assert EventJobStore(path).claim() is None


def test_jobs_bounded_failure_and_success_idempotency(tmp_path):
    store = EventJobStore(tmp_path / "m.db")
    store.enqueue("u", "default", "s", "m")
    for i in range(3):
        job = store.claim(now=10**11)
        store.finish(job, "invalid_json")
    assert store.claim(now=10**11) is None
    store.enqueue("v", "default", "s", "m")
    job = store.claim()
    store.finish(job)
    store.enqueue("v", "default", "s", "m")
    assert store.claim(now=10**11) is None


def service(tmp_path, llm):
    store = EventMemoryStore(tmp_path / "m.db")
    return EventMemoryService(
        store, ObservationStore(tmp_path / "m.db"), extractor=EventExtractor(llm)
    )


def test_correction_survives_failed_extraction(tmp_path):
    svc = service(tmp_path, Sequence(LLMResponse(content=""), LLMResponse(content="")))
    asyncio.run(
        svc.capture_user_message(MemoryScope(user_id="u"), "s", msg("那是下午补觉补的"))
    )
    events = svc.store.query("u")
    assert len(events) == 1 and events[0].kind == "user_correction"
    assert events[0].details[0].protected
    assert "下午补觉" in events[0].core_summary


def test_worker_resolves_persisted_source_and_finishes(tmp_path):
    svc = service(tmp_path, Sequence([]))
    session = SimpleNamespace(
        user_id="u", agent_id="default", id="s", messages=[msg("你好")]
    )

    async def load(_):
        return session

    svc.session_loader = load
    svc.enqueue(MemoryScope(user_id="u"), "s", session.messages[0])
    assert asyncio.run(svc.process_pending())
    assert svc.jobs.claim(now=10**11) is None


def test_deletion_cancels_inflight_extraction_before_persist(tmp_path):
    async def scenario():
        started = asyncio.Event()

        class Slow(LLMClient):
            async def complete(self, *args, **kwargs):
                started.set()
                await asyncio.sleep(100)

        svc = service(tmp_path, Slow())
        session = SimpleNamespace(
            user_id="u", agent_id="default", id="s", messages=[msg("那是下午补觉补的")]
        )

        async def load(_):
            return session

        svc.session_loader = load
        svc.enqueue(MemoryScope(user_id="u"), "s", session.messages[0])
        worker = asyncio.create_task(svc.process_pending())
        await started.wait()
        await svc.cancel_user("u", "default")
        svc.store.delete_user("u")
        await worker
        assert not svc.store.query("u")
        assert svc.jobs.claim() is None

    asyncio.run(scenario())


def test_explicit_update_key_closes_existing_plan(tmp_path):
    candidate = {
        "event_key": "new_wording",
        "updates_event_key": "dinner",
        "title": "吃完回家",
        "core_summary": "晚餐已结束",
        "status": "completed",
    }
    svc = service(tmp_path, Sequence([candidate]))
    svc.store.upsert_candidate(
        user_id="u",
        agent_id="default",
        candidate=EventCandidate(
            event_key="dinner", title="晚餐", core_summary="计划晚餐", status="planned"
        ),
        origin=OriginClass.owner,
        source_ref="old",
    )
    asyncio.run(
        svc.capture_user_message(MemoryScope(user_id="u"), "s", msg("吃完饭到家了"))
    )
    events = svc.store.query("u")
    assert (
        len(events) == 1
        and events[0].event_key == "dinner"
        and events[0].status == EventStatus.completed
    )


def test_related_reason_survives_unrelated_future_plans(tmp_path):
    store = EventMemoryStore(tmp_path / "m.db")
    now = datetime.now(UTC)
    for i in range(16):
        store.upsert_candidate(
            user_id="u",
            agent_id="default",
            candidate=EventCandidate(
                event_key=f"travel{i}",
                title="国庆旅游",
                core_summary="将来旅行",
                occurred_start=now + timedelta(days=i + 1),
                status="planned",
            ),
            origin=OriginClass.owner,
            source_ref=str(i),
        )
    store.upsert_candidate(
        user_id="u",
        agent_id="default",
        candidate=EventCandidate(
            event_key="early_sleep",
            title="提前睡觉",
            core_summary="为了班会提前睡觉",
            occurred_start=now - timedelta(days=2),
        ),
        origin=OriginClass.owner,
        source_ref="sleep",
        now=now - timedelta(days=2),
    )
    assert (
        store.recall("u", query="睡觉为什么提前", limit=6)[0].event_key == "early_sleep"
    )
    assert store.active("u", now=now) == []


def test_delayed_retry_cannot_restore_old_plan(tmp_path):
    store = EventMemoryStore(tmp_path / "m.db")
    now = datetime.now(UTC)
    completed = EventCandidate(
        event_key="dinner", title="晚餐", core_summary="已经吃完", status="completed"
    )
    store.upsert_candidate(
        user_id="u",
        agent_id="default",
        candidate=completed,
        origin=OriginClass.owner,
        source_ref="new",
        now=now,
    )
    old = completed.model_copy(
        update={"status": EventStatus.planned, "core_summary": "准备去吃"}
    )
    store.upsert_candidate(
        user_id="u",
        agent_id="default",
        candidate=old,
        origin=OriginClass.owner,
        source_ref="old",
        now=now - timedelta(hours=1),
    )
    result = store.query("u")[0]
    assert result.status == EventStatus.completed and result.core_summary == "已经吃完"
    assert "old" in result.source_refs


@pytest.mark.parametrize(
    "text", ["那是什么？", "不是吧", "那是我买的杯子吗", "那是不是明天的事"]
)
def test_questions_do_not_trigger_negative_feedback(text):
    assert not is_correction(text)


def test_sleep_total_is_separate_from_main_episode_and_nap_unknown():
    metric = SimpleNamespace(
        metric_type="SLEEP", day="2026-09-14", value1=575, updated_at=1
    )
    score = SimpleNamespace(
        sleep_day="2026-09-14",
        session_start="2026-09-13T17:41:00Z",
        session_end="2026-09-14T00:42:00Z",
    )
    result = sleep_evidence([metric], [score])[0]
    assert result["daily_total_sleep_minutes"] == 575
    assert result["main_sleep_episode"]["interval_minutes"] == 421
    assert result["nap_minutes"] is None


@pytest.mark.parametrize(
    "draft,answer",
    [
        ("配着傍晚天气从学校骑回来很舒服", "是下午的运动，我刚才理解错了。"),
        ("明天班会已经开完了，这周不用早起", "你昨晚是为了今天的班会早点睡。"),
        ("昨晚睡了九个半小时", "那是当天累计睡眠，不能都算作昨晚。"),
    ],
)
def test_ordinary_reply_is_corrected_before_return(draft, answer):
    llm = Sequence(
        LLMResponse(content=draft), {"verdict": "rewrite", "message": answer}
    )
    result = asyncio.run(
        BasicAgentRunner(llm, grounding_check=True).run(context("那是下午的"))
    )
    assert result.text == answer
    assert "correction" in llm.calls[-1][-1]["content"]


def test_failed_check_never_streams_bad_draft():
    async def scenario():
        llm = Sequence(
            LLMResponse(content="昨晚睡了9.5小时"),
            LLMResponse(content=""),
            LLMResponse(content=""),
        )
        events = [
            e
            async for e in BasicAgentRunner(llm, grounding_check=True).run_stream(
                context("那是下午补觉补的")
            )
        ]
        assert all("9.5" not in e.content for e in events)
        assert events[-1].done

    asyncio.run(scenario())


def test_guard_fetches_readonly_weather_before_answer():
    class Weather(Tool):
        name = "weather"
        description = "weather"
        parameters = {"type": "object", "properties": {}}
        calls = 0

        async def execute(self, **kwargs):
            self.calls += 1
            return '{"temperature":28}'

    weather = Weather()
    llm = Sequence(
        LLMResponse(content="看了，今天晴天。"),
        {"verdict": "needs_tool", "tool_name": "weather", "tool_args": {}},
        {"verdict": "rewrite", "message": "查到了，现在28度。"},
    )
    result = asyncio.run(
        BasicAgentRunner(llm, grounding_check=True).run(
            context("你自己看看天气呗", [weather])
        )
    )
    assert weather.calls == 1 and result.tool_results[0]["name"] == "weather"
    assert result.text == "查到了，现在28度。"


def test_guard_does_not_execute_mutating_tool():
    llm = Sequence(
        LLMResponse(content="今天帮你取消了班会"),
        {
            "verdict": "needs_tool",
            "tool_name": "schedule",
            "tool_args": {"action": "cancel"},
        },
    )
    result = asyncio.run(
        BasicAgentRunner(llm, grounding_check=True).run(context("今天有什么安排"))
    )
    assert result.tool_results == [] and "取消了" not in result.text


@pytest.mark.parametrize(
    "text",
    [
        "那是下午补觉补的",
        "那都是下午的运动了",
        "傍晚？你是怎么理解傍晚的",
        "我都出发了，肯定知道有班会啊",
    ],
)
def test_natural_corrections_are_negative_feedback(text):
    from app.proactive.engine import ProactiveEngine

    assert is_correction(text)
    assert ProactiveEngine._relationship_preference(text) == "continuity_error"


def test_health_shares_require_continuity_check():
    from app.proactive.engine import ProactiveEngine
    from app.proactive.models import ProactiveDecision, ProactiveCategory

    d = ProactiveDecision(
        user_id="u",
        agent_id="default",
        trigger_type="event",
        should_message=True,
        category=ProactiveCategory.health_insight,
        message="昨晚睡了9.5小时",
    )
    assert ProactiveEngine._continuity_high_risk(d)


def test_unanswered_question_survives_assistant_traffic():
    from app.proactive.context import ProactiveContextBuilder

    now = datetime.now(UTC)
    messages = [
        msg(
            "写东西放音乐还是安静？",
            "assistant",
            "old_question",
            now - timedelta(days=1),
        )
    ]
    messages += [
        msg("今天同步了步数。", "assistant", f"a{i}", now - timedelta(minutes=60 - i))
        for i in range(20)
    ]
    session = SimpleNamespace(
        agent_id="default", end_reason=None, updated_at=now, messages=messages
    )

    class Sessions:
        async def list_by_user(self, _):
            return [session]

    builder = ProactiveContextBuilder.__new__(ProactiveContextBuilder)
    builder.session_service = Sessions()
    builder.conversation_tail_messages = 4
    builder.conversation_user_exchanges = 12
    builder.conversation_continuity_hours = 72
    builder.conversation_fresh_seconds = 1800
    builder.conversation_stale_seconds = 21600
    signals = asyncio.run(builder._conversation_signals(MemoryScope(user_id="u"), now))
    prior = next(s for s in signals if s.id == "conversation_prior_topics")
    assert prior.value["questions"][0]["id"] == "old_question"
    assert len(prior.value["statements"]) == 8


def test_repeated_worker_crashes_stop_after_attempt_budget(tmp_path):
    store = EventJobStore(tmp_path / "m.db")
    store.enqueue("u", "default", "s", "m")
    for i in range(3):
        assert store.claim(now=i * 301) is not None
    assert store.claim(now=1000) is None


@pytest.mark.parametrize(
    "response,skip",
    [
        ({"skip": True, "confidence": 0.99, "source_message_id": "m"}, True),
        ({"skip": True, "confidence": 0.99, "source_message_id": "invented"}, False),
        ({"skip": False, "confidence": 0.99, "source_message_id": "m"}, False),
        (RuntimeError("unavailable"), False),
    ],
)
def test_schedule_requires_owner_reference_and_preserves_on_failure(response, skip):
    now = datetime.now(UTC)
    session = SimpleNamespace(
        agent_id="default",
        end_reason=None,
        updated_at=now,
        messages=[msg("已经出门去班会了", at=now)],
    )

    class Sessions:
        async def list_by_user(self, _):
            return [session]

    scheduler = ScheduleScheduler(
        None, None, llm=Sequence(response), session_service=Sessions()
    )
    assert (
        asyncio.run(
            scheduler._already_progressed("u", "default", {"title": "班会"}, now)
        )
        is skip
    )
