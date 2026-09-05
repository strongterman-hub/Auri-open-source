from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.agent.llm import LLMClient, LLMResponse, ToolCall
from app.agent.tools import Tool
from app.config import Settings
from app.memory.file_store import FileMemoryStore
from app.memory.intents import IntentStore
from app.memory.models import MemoryScope
from app.memory.told import ToldFeedback, ToldStore
from app.observation.store import ObservationStore
from app.proactive.delivery import ProactiveDelivery
from app.proactive.context import (
    Interruptibility,
    ProactiveAuditLogger,
    SignalFreshness,
    SituationSignal,
    SituationSnapshot,
)
from app.proactive.engine import ProactiveEngine
from app.proactive.models import (
    ConversationIntent,
    DeliveryChannel,
    ProactiveCategory,
    ProactiveDecision,
    ProactivePhase,
    TriggerType,
)
from app.proactive.pacing import ProactivePacingStore
from app.proactive.preferences import PreferenceStore
from app.proactive.profile import PROFILE_SLOTS, ProfileStore
from app.proactive.push import NullPushSender
from app.proactive.settings import ProactiveSettingsStore
from app.proactive.store import ProactiveStore
from app.services.agent_service import AgentService
from app.services.memory_service import MemoryService
from app.services.observation_service import ObservationService
from app.services.presence_service import PresenceService
from app.services.session_service import SessionService
from app.session.models import Session, SessionCreate
from app.session.store import FileSessionStore


class FakeProactiveLLM(LLMClient):
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[list[dict]] = []

    async def complete(self, messages, tools=None, max_tokens=None):
        self.calls.append(messages)
        return LLMResponse(content=self.content)

    async def stream(self, messages, tools=None, max_tokens=None):
        raise NotImplementedError


class SequenceProactiveLLM(LLMClient):
    """Return a queue of onboarding plans, one per LLM call."""

    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)

    async def complete(self, messages, tools=None, max_tokens=None):
        content = self.responses.pop(0) if self.responses else self.responses[-1]
        return LLMResponse(content=content)

    async def stream(self, messages, tools=None, max_tokens=None):
        raise NotImplementedError


class ToolCallingProactiveLLM(LLMClient):
    """Call one fake tool first, then return a proactive decision JSON."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools=None, max_tokens=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[
                    ToolCall(
                        id="tool-1",
                        name="fake_health",
                        arguments={"operation": "summary"},
                    )
                ],
            )
        return LLMResponse(
            content=(
                '{"category": "health_insight", '
                '"message": "今天心率正常"}'
            )
        )

    async def stream(self, messages, tools=None, max_tokens=None):
        raise NotImplementedError


class FakeHealthTool(Tool):
    name = "fake_health"
    description = "Return a fake health summary."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> str:
        return '{"steps": 8000, "resting_heart_rate": 62}'


class FakeXiaomiService:
    def __init__(self, bound: bool = True) -> None:
        self.bound = bound

    def status(self, user_id: str) -> dict:
        return {
            "bound": self.bound,
            "last_sync_at": 1788101525243 if self.bound else None,
            "available_data_types": ["daily_activity", "heart_rate"] if self.bound else [],
        }


class StaticContextBuilder:
    def __init__(self, snapshot: SituationSnapshot) -> None:
        self.snapshot = snapshot

    async def build(self, *_args, **_kwargs) -> SituationSnapshot:
        return self.snapshot


def _build_engine(
    tmp_dir: Path,
    llm_content: str,
    *,
    daily_budget: int = 3,
    cooldown_seconds: int = 0,
    quiet_hours: str = "",
    onboarding_enabled: bool = False,
    pacing_enabled: bool = False,
    health_store=None,
    trending_service=None,
) -> tuple[ProactiveEngine, SessionService, ToldStore, ProactiveStore, PresenceService]:
    settings = Settings(
        data_dir=tmp_dir,
        proactive_enabled=True,
        proactive_daily_budget=daily_budget,
        proactive_cooldown_seconds=cooldown_seconds,
        proactive_quiet_hours=quiet_hours,
        proactive_onboarding_enabled=onboarding_enabled,
        proactive_pacing_enabled=pacing_enabled,
    )
    memory_service = MemoryService(FileMemoryStore(tmp_dir / "mem"))
    observation_service = ObservationService(ObservationStore(tmp_dir / "memory.db"))
    session_service = SessionService(FileSessionStore(tmp_dir / "sessions"))
    told_store = ToldStore(tmp_dir / "told.db")
    intent_store = IntentStore(tmp_dir / "intents.db")
    store = ProactiveStore(tmp_dir / "decisions.db")
    presence = PresenceService()
    settings_store = ProactiveSettingsStore(tmp_dir / "proactive.db")
    settings_store.set_enabled("u1", "default", True)
    preference_store = PreferenceStore(tmp_dir / "preferences.db")
    profile_store = ProfileStore(tmp_dir / "profile.db")
    pacing_store = ProactivePacingStore(tmp_dir / "pacing.db")
    engine = ProactiveEngine(
        llm=FakeProactiveLLM(llm_content),
        memory_service=memory_service,
        observation_service=observation_service,
        session_service=session_service,
        told_store=told_store,
        intent_store=intent_store,
        store=store,
        delivery=ProactiveDelivery(session_service, presence, NullPushSender(), 180),
        settings=settings,
        presence=presence,
        settings_store=settings_store,
        preference_store=preference_store,
        profile_store=profile_store,
        pacing_store=pacing_store,
        health_store=health_store,
        trending_service=trending_service,
    )
    return engine, session_service, told_store, store, presence


def test_engine_delivers_proactive_message_to_session(tmp_dir: Path) -> None:
    engine, session_service, told_store, _store, presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "sleep_tip", "message": "昨晚睡得有点晚", "importance": 7}',
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )
    presence.heartbeat("u1", "default")
    presence.register_device("u1", "default", "device-1")

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert decision is not None
    assert decision.should_message is True
    assert decision.delivery_channel is DeliveryChannel.push
    assert told_store.has_told(decision.insight_key, "u1") is True

    sessions = asyncio.run(session_service.list_by_user("u1"))
    assert len(sessions) == 1
    assistant_messages = [
        message for message in sessions[0].messages if message["role"] == "assistant"
    ]
    assert assistant_messages[-1]["id"] == decision.id
    assert assistant_messages[-1]["content"] == "昨晚睡得有点晚"


def test_maybe_onboard_returns_cleanly_when_proactive_is_globally_disabled(
    tmp_dir: Path,
) -> None:
    engine, _session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"category": "explore", "message": "不会发送"}',
    )
    engine.settings.proactive_enabled = False

    assert asyncio.run(engine.maybe_onboard("u1", "default")) is None


def test_proactive_decision_receives_current_time_message_time_and_event_timeline(
    tmp_dir: Path,
) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"category": "explore", "message": "聊聊近况"}',
    )
    session = asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )
    session.messages.append(
        {
            "role": "user",
            "content": "我刚搬进新家",
            "timestamp": "2026-09-02T03:29:00+00:00",
        }
    )
    asyncio.run(session_service.save(session))

    class FakeEventMemoryService:
        async def build_context(self, *_args, **_kwargs):
            return "EVENT move_new_home_2026_09 @ 2026-09-02T11:29:00+08:00"

    engine.event_memory_service = FakeEventMemoryService()
    scope = MemoryScope(user_id="u1", agent_id="default")
    snapshot = asyncio.run(engine.memory_service.snapshot(scope))

    asyncio.run(
        engine._decide(
            scope,
            snapshot,
            [],
            [ProactiveCategory.explore],
            [],
        )
    )

    prompt = engine.llm.calls[-1][1]["content"]
    assert "Current local time (authoritative):" in prompt
    assert "[2026-09-02T11:29:00+08:00] 用户: 我刚搬进新家" in prompt
    assert "EVENT move_new_home_2026_09 @ 2026-09-02T11:29:00+08:00" in prompt


def test_engine_does_not_enforce_daily_budget(tmp_dir: Path) -> None:
    engine, session_service, _told, store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 5}',
        daily_budget=1,
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    first = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))
    second = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert first is not None and first.should_message is True
    assert second is not None and second.should_message is True
    assert store.count_today("u1") == 2


def test_engine_respects_cooldown(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 5}',
        daily_budget=10,
        cooldown_seconds=3600,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    first = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))
    second = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert first is not None and first.should_message is True
    assert second is None


def test_engine_tick_iterates_users(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 5}',
        cooldown_seconds=0,
    )
    asyncio.run(session_service.create(SessionCreate(user_id="u1", agent_id="default")))

    decisions = asyncio.run(engine.tick())

    assert len(decisions) == 1
    assert decisions[0].user_id == "u1"


def test_presence_and_inbox(tmp_dir: Path) -> None:
    presence = PresenceService()
    assert presence.is_online("u1") is False
    presence.heartbeat("u1", "default")
    assert presence.is_online("u1", "default", ttl_seconds=180) is True

    store = ProactiveStore(tmp_dir / "decisions.db")
    decision = ProactiveDecision(
        user_id="u1",
        trigger_type=TriggerType.time,
        should_message=True,
        message="hello",
    )
    store.add(decision)
    assert [item.id for item in store.list("u1")] == [decision.id]
    assert store.acknowledge("u1", "default", [decision.id]) == 1
    assert store.list("u1") == []


def test_quiet_hours_gate(tmp_dir: Path) -> None:
    engine, _session, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 5}',
        quiet_hours="23-08",
    )

    assert engine._within_quiet_hours(datetime(2026, 8, 22, 7, 0, 0)) is True
    assert engine._within_quiet_hours(datetime(2026, 8, 22, 23, 0, 0)) is True
    assert engine._within_quiet_hours(datetime(2026, 8, 22, 12, 0, 0)) is False


def test_unhelpful_feedback_blocks_same_insight(tmp_dir: Path) -> None:
    engine, session_service, told_store, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "sleep_tip", "message": "提醒", "importance": 5}',
        daily_budget=10,
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    first = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))
    assert first is not None and first.should_message is True

    told_store.update_feedback(
        first.insight_key, "u1", "default", ToldFeedback.unhelpful
    )
    second = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert second is not None
    assert second.should_message is False


def test_offline_delivery_uses_push_channel(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 8}',
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )
    presence.register_device("u1", "default", "device-1")

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert decision is not None
    assert decision.delivery_channel is DeliveryChannel.push


def test_user_can_disable_proactive_messages(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 5}',
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )
    engine.settings_store.set_enabled("u1", "default", False)

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert decision is None


def test_daily_message_carries_category_and_phase(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert decision is not None
    assert decision.phase is ProactivePhase.daily
    assert decision.category is not None
    assert decision.category.value in {
        "health_insight",
        "health_care",
        "weather",
        "explore",
        "goal_reminder",
        "memory_recall",
    }


def test_empty_message_is_not_sent(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"category": "weather", "message": ""}',
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert decision is not None
    assert decision.should_message is False


def test_reply_attribution_records_preference(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))
    assert decision is not None
    category = decision.category.value

    asyncio.run(engine.record_reply("u1", "default", decision.id))

    assert decision.replied is True
    stats = {item["category"]: item for item in engine.preference_store.stats("u1")}
    assert stats[category]["replied"] == 1


def test_available_categories_includes_all_daily(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
    )

    candidates = engine._available_categories("u1", "default", "time", [])

    assert ProactiveCategory.health_insight in candidates
    assert ProactiveCategory.memory_recall in candidates
    assert ProactiveCategory.explore in candidates


def test_onboarding_asks_profile_question_and_reply_fills_slot(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "我是 Auri，先告诉我怎么称呼你？"}',
        onboarding_enabled=True,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    assert asyncio.run(engine.current_phase("u1")) is ProactivePhase.onboarding

    decision = asyncio.run(
        engine.evaluate_onboarding("u1", "default", TriggerType.time)
    )

    assert decision is not None
    assert decision.phase is ProactivePhase.onboarding
    assert decision.category is ProactiveCategory.profile_question
    assert decision.insight_key == "profile_name"
    assert "Auri" in decision.message
    assert "怎么称呼" in decision.message
    assert engine.profile_store.onboarding_state("u1")[1] == 1

    asyncio.run(engine.record_reply("u1", "default", decision.id))

    assert "name" in engine.profile_store.completed_slots("u1")


def test_onboarding_exits_when_profile_complete(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
    )
    completed = list(PROFILE_SLOTS) + ["capability_intro", "xiaomi_connect", "proactive_enable"]
    for slot in completed:
        engine.profile_store.mark_slot_completed("u1", "default", slot)

    assert engine._transition_onboarding_phase("u1", "default") == "slow"


def test_existing_user_skips_onboarding(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
        cooldown_seconds=0,
    )
    session = asyncio.run(
        session_service.ensure(SessionCreate(user_id="u1", agent_id="default"))
    )
    session.messages.append(
        {
            "id": "m1",
            "role": "user",
            "content": "hi",
            "timestamp": "2026-08-23T00:00:00+00:00",
        }
    )
    session.messages.append(
        {
            "id": "m2",
            "role": "assistant",
            "content": "你好",
            "timestamp": "2026-08-23T00:00:01+00:00",
        }
    )
    asyncio.run(session_service.save(session))

    assert engine.onboarding_phase("u1", "default") == "dense"

    decision = asyncio.run(
        engine.evaluate_onboarding("u1", "default", TriggerType.time)
    )

    assert decision is not None
    assert decision.phase is ProactivePhase.onboarding


def test_decision_captures_push_message(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "sleep_tip", '
        '"message": "昨晚睡得有点晚，今天要不要早点休息？", '
        '"push_message": "昨晚睡得有点晚", "importance": 7}',
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert decision is not None
    assert decision.push_message == "昨晚睡得有点晚"


def test_active_conversation_does_not_suppress_daily(
    tmp_dir: Path,
) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
        cooldown_seconds=0,
    )
    session = asyncio.run(
        session_service.ensure(SessionCreate(user_id="u1", agent_id="default"))
    )
    session.messages.append(
        {
            "id": "m1",
            "role": "user",
            "content": "我还有一个问题",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    asyncio.run(session_service.save(session))

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert decision is not None
    assert decision.should_message is True


def test_active_conversation_recent_window_does_not_suppress_daily(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
        cooldown_seconds=0,
    )
    now = datetime.now(timezone.utc)
    session = asyncio.run(
        session_service.ensure(SessionCreate(user_id="u1", agent_id="default"))
    )
    session.messages.extend(
        [
            {
                "id": "m1",
                "role": "user",
                "content": "看看我最近的数据",
                "timestamp": now.isoformat(),
            },
            {
                "id": "m2",
                "role": "assistant",
                "content": "好的，我已经看完。",
                "timestamp": now.isoformat(),
            },
        ]
    )
    asyncio.run(session_service.save(session))

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert decision is not None
    assert decision.should_message is True


def test_onboarding_waits_for_pending_slot(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
    )
    engine.settings.proactive_onboarding_cooldown_seconds = 0
    engine.llm = SequenceProactiveLLM(
        [
            '{"slot": "name", "message": "怎么称呼你？"}',
            '{"slot": "capability_intro", "message": "我可以帮你查天气、看健康数据。"}',
        ]
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    first = asyncio.run(
        engine.evaluate_onboarding("u1", "default", TriggerType.time)
    )
    second = asyncio.run(
        engine.evaluate_onboarding("u1", "default", TriggerType.time)
    )
    third = asyncio.run(
        engine.evaluate_onboarding("u1", "default", TriggerType.time)
    )

    assert first is not None
    assert first.category is ProactiveCategory.profile_question
    assert second is not None
    assert second.insight_key == "capability_intro"
    assert third is not None
    assert third.insight_key == "xiaomi_connect"
    assert engine.profile_store.dense_pending_count("u1", "default") == 2


def test_onboarding_pending_slot_expires_and_continues(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
    )
    engine.settings.proactive_onboarding_cooldown_seconds = 0
    engine.llm = SequenceProactiveLLM(
        [
            '{"slot": "name", "message": "怎么称呼你？"}',
            '{"slot": "capability_intro", "message": "我可以帮你查天气、看健康数据。"}',
        ]
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    first = asyncio.run(
        engine.evaluate_onboarding("u1", "default", TriggerType.time)
    )
    second = asyncio.run(
        engine.evaluate_onboarding("u1", "default", TriggerType.time)
    )
    assert first is not None
    assert second is not None
    assert second.insight_key == "capability_intro"

    engine.llm = FakeAssessmentLLM(True)
    asyncio.run(engine.record_reply("u1", "default", first.id, "叫我 Terman"))
    assert engine.profile_store.dense_pending_count("u1", "default") == 0


def test_daily_runs_alongside_onboarding(tmp_dir: Path) -> None:
    engine, session_service, told_store, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "hr_spike", "message": "昨晚静息心率偏高", "importance": 7}',
        onboarding_enabled=True,
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    assert asyncio.run(engine._is_onboarding("u1", "default")) is True

    decision = asyncio.run(engine.evaluate_daily("u1", "default", TriggerType.event))

    assert decision is not None
    assert decision.should_message is True
    assert told_store.has_told(decision.insight_key, "u1") is True


def test_proactive_delivery_marks_message_metadata(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(
        engine.evaluate_daily("u1", "default", TriggerType.time)
    )
    sessions = asyncio.run(session_service.list_by_user("u1"))
    assistant_messages = [
        message for message in sessions[0].messages if message["role"] == "assistant"
    ]

    assert decision is not None
    assert assistant_messages[-1]["proactive"] is True
    assert assistant_messages[-1]["decision_id"] == decision.id


class FakeAssessmentLLM(LLMClient):
    def __init__(self, answered: bool) -> None:
        self.answered = answered

    async def complete(self, messages, tools=None, max_tokens=None):
        system = messages[0]["content"] if messages else ""
        if "answered" in system:
            return LLMResponse(
                content=f'{{"answered": {str(self.answered).lower()}}}'
            )
        return LLMResponse(content='{"slot": "name", "message": "怎么称呼你？"}')

    async def stream(self, messages, tools=None, max_tokens=None):
        raise NotImplementedError


def test_onboarding_reply_not_answering_does_not_fill_slot(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
    )
    engine.llm = FakeAssessmentLLM(False)
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))
    asyncio.run(
        engine.record_reply(
            "u1",
            "default",
            decision.id,
            "你拿不到我的 BMI 吗",
        )
    )

    assert decision is not None
    assert "name" not in engine.profile_store.completed_slots("u1")


def test_onboarding_reply_answering_fills_slot(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
    )
    engine.llm = FakeAssessmentLLM(True)
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(
        engine.evaluate_onboarding("u1", "default", TriggerType.time)
    )
    asyncio.run(
        engine.record_reply(
            "u1",
            "default",
            decision.id,
            "叫我 Terman",
        )
    )

    assert decision is not None
    assert "name" in engine.profile_store.completed_slots("u1")


def test_agent_service_attributes_only_marked_proactive_reply() -> None:
    calls: list[tuple[str, str, str, str | None]] = []

    async def hook(user_id: str, agent_id: str, message_id: str, text: str | None) -> None:
        calls.append((user_id, agent_id, message_id, text))

    service = AgentService(
        session_service=None,  # type: ignore[arg-type]
        memory_service=None,  # type: ignore[arg-type]
        runner=None,  # type: ignore[arg-type]
        tool_factory=None,  # type: ignore[arg-type]
        proactive_reply_hook=hook,
    )
    now = datetime.now(timezone.utc)

    marked = Session(
        id="s1",
        user_id="u1",
        messages=[
            {
                "id": "m1",
                "role": "assistant",
                "content": "你平时作息如何？",
                "timestamp": now.isoformat(),
                "proactive": True,
            }
        ],
        created_at=now,
        updated_at=now,
    )
    asyncio.run(service._attribute_proactive_reply(marked, "我一般十点睡"))

    assert len(calls) == 1
    assert calls[0][0] == "u1"
    assert calls[0][1] == "default"
    assert calls[0][2] == "m1"
    assert calls[0][3] == "我一般十点睡"


def test_agent_service_skips_normal_assistant_reply() -> None:
    calls: list[tuple[str, str, str, str | None]] = []

    async def hook(user_id: str, agent_id: str, message_id: str, text: str | None) -> None:
        calls.append((user_id, agent_id, message_id, text))

    service = AgentService(
        session_service=None,  # type: ignore[arg-type]
        memory_service=None,  # type: ignore[arg-type]
        runner=None,  # type: ignore[arg-type]
        tool_factory=None,  # type: ignore[arg-type]
        proactive_reply_hook=hook,
    )
    now = datetime.now(timezone.utc)

    normal = Session(
        id="s2",
        user_id="u1",
        messages=[
            {
                "id": "m1",
                "role": "assistant",
                "content": "普通回复",
                "timestamp": now.isoformat(),
            }
        ],
        created_at=now,
        updated_at=now,
    )
    asyncio.run(service._attribute_proactive_reply(normal, "继续聊"))

    assert calls == []


def test_onboarding_suppressed_when_asleep(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
    )
    engine._probably_asleep = lambda *args, **kwargs: True
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert decision is None


def test_daily_still_respects_quiet_hours(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
        onboarding_enabled=False,
    )
    engine._within_quiet_hours = lambda *args, **kwargs: True
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(engine.evaluate_user("u1", "default", TriggerType.time))

    assert decision is None


def test_daily_decision_can_call_tools_before_deciding(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": false, "insight_key": "unused", "message": "unused", "importance": 1}',
        cooldown_seconds=0,
    )
    engine.llm = ToolCallingProactiveLLM()
    engine.tool_factory = lambda scope: [FakeHealthTool()]
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(engine.evaluate_daily("u1", "default", TriggerType.event))

    assert decision is not None
    assert decision.should_message is True
    assert decision.message == "今天心率正常"
    assert decision.category is ProactiveCategory.health_insight
    assert decision.tool_calls == [
        {
            "name": "fake_health",
            "argument_keys": ["operation"],
            "status": "ok",
        }
    ]
    assert engine.llm.calls == 2


def test_do_not_interrupt_snapshot_skips_llm_and_writes_audit(
    tmp_dir: Path,
) -> None:
    engine, _sessions, _told, store, _presence = _build_engine(
        tmp_dir,
        '{"category": "explore", "message": "不应被调用"}',
        cooldown_seconds=0,
    )
    snapshot = SituationSnapshot(
        user_id="u1",
        generated_at=datetime.now(timezone.utc),
        timezone="Asia/Shanghai",
        signals=[
            SituationSignal(
                id="health_sleep_stage_latest",
                source="health",
                kind="sleep_stage",
                value={"value1": 2},
                observed_at=datetime.now(timezone.utc),
                freshness=SignalFreshness.fresh,
            )
        ],
        interruptibility=Interruptibility.do_not_interrupt,
        interruptibility_reasons=["新鲜睡眠分期显示用户正在睡眠"],
        interruptibility_evidence=["health_sleep_stage_latest"],
    )
    engine.context_builder = StaticContextBuilder(snapshot)
    audit_path = tmp_dir / "logs" / "proactive_context.jsonl"
    engine.audit_logger = ProactiveAuditLogger(audit_path)

    decision = asyncio.run(
        engine.evaluate_daily("u1", "default", TriggerType.time)
    )

    assert decision is not None
    assert decision.should_message is False
    assert decision.context_snapshot_id == snapshot.id
    assert decision.evidence_refs == ["health_sleep_stage_latest"]
    assert len(engine.llm.calls) == 0
    assert store.get("u1", "default", decision.id) is decision
    assert audit_path.exists()


def test_explicit_silent_decision_cannot_send_nonempty_message(
    tmp_dir: Path,
) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": false, "category": "explore", '
        '"message": "这条消息不应发送", "silence_reason": "用户可能在忙"}',
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(
        engine.evaluate_daily("u1", "default", TriggerType.time)
    )

    assert decision is not None
    assert decision.should_message is False
    assert decision.message == ""
    assert decision.delivery_channel is DeliveryChannel.none


def test_ungrounded_current_activity_claim_is_rejected(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "category": "health_care", '
        '"situation_summary": "用户可能在开会", "situation_confidence": 0.9, '
        '"evidence_refs": ["missing"], "message": "看起来你正在开会，聊聊吗？"}',
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )
    snapshot = SituationSnapshot(
        user_id="u1",
        generated_at=datetime.now(timezone.utc),
        timezone="Asia/Shanghai",
        signals=[
            SituationSignal(
                id="time_now",
                source="time",
                kind="current_local_time",
                value={},
                observed_at=datetime.now(timezone.utc),
                freshness=SignalFreshness.fresh,
            )
        ],
    )
    engine.context_builder = StaticContextBuilder(snapshot)

    decision = asyncio.run(
        engine.evaluate_daily("u1", "default", TriggerType.time)
    )

    assert decision is not None
    assert decision.should_message is False
    assert decision.message == ""
    assert decision.evidence_refs == []
    assert decision.silence_reason == "当前状态断言缺少足够新鲜且可信的证据"


def test_grounded_current_activity_claim_can_send(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "category": "health_care", '
        '"situation_summary": "用户刚说自己在整理房间", '
        '"situation_confidence": 0.9, "evidence_refs": ["conversation_1"], '
        '"decision_reason": "顺着当前活动轻问一句", '
        '"message": "你正在整理房间，要不要我帮你列个收纳顺序？"}',
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )
    snapshot = SituationSnapshot(
        user_id="u1",
        generated_at=datetime.now(timezone.utc),
        timezone="Asia/Shanghai",
        signals=[
            SituationSignal(
                id="conversation_1",
                source="conversation",
                kind="message",
                value={"role": "user", "text": "我正在整理房间"},
                observed_at=datetime.now(timezone.utc),
                freshness=SignalFreshness.fresh,
            )
        ],
    )
    engine.context_builder = StaticContextBuilder(snapshot)

    decision = asyncio.run(
        engine.evaluate_daily("u1", "default", TriggerType.time)
    )

    assert decision is not None
    assert decision.should_message is True
    assert decision.evidence_refs == ["conversation_1"]
    assert decision.context_snapshot_id == snapshot.id


class FakeSleepHealthStore:
    """Minimal health store returning one synthetic SLEEP_STAGE sample."""

    def __init__(self, stage: float, bucket_start: str) -> None:
        self.stage = stage
        self.bucket_start = bucket_start

    def latest_sample(self, user_id: str, sample_type: str):
        from types import SimpleNamespace

        return SimpleNamespace(
            bucket_start=self.bucket_start,
            value1=self.stage,
        )


def test_memory_recall_is_available_daily_category(tmp_dir: Path) -> None:
    from app.proactive.engine import DAILY_CATEGORIES

    assert ProactiveCategory.memory_recall in DAILY_CATEGORIES


def test_llm_chooses_category_from_shortlist(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"category": "weather", "should_message": true, '
        '"insight_key": "wx", "message": "今天会下雨", "importance": 7}',
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(
        engine.evaluate_daily("u1", "default", TriggerType.event, "weather")
    )

    assert decision is not None
    assert decision.category is ProactiveCategory.weather


def test_category_falls_back_when_llm_omits_category(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(engine.evaluate_daily("u1", "default", TriggerType.time))

    assert decision is not None
    assert decision.category is not None
    assert decision.category in {
        ProactiveCategory.health_insight,
        ProactiveCategory.health_care,
        ProactiveCategory.weather,
        ProactiveCategory.explore,
        ProactiveCategory.goal_reminder,
        ProactiveCategory.memory_recall,
    }


def test_pacing_backs_off_on_unanswered(tmp_dir: Path) -> None:
    engine, session_service, _told, store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", '
        '"importance": 7, "conversation_intent": "direct_question"}',
        pacing_enabled=True,
    )
    engine.settings.proactive_pacing_min_cooldown_seconds = 3600
    engine.settings.proactive_pacing_max_cooldown_seconds = 3600
    engine.settings.proactive_pacing_backoff_growth = 2.0
    engine.settings.proactive_pacing_max_unreplied = 5
    engine.settings.proactive_pacing_max_effective_cooldown_seconds = 86400
    engine.settings.proactive_engagement_settlement_seconds = 0
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    first = asyncio.run(engine.evaluate_daily("u1", "default", TriggerType.time))
    assert first is not None
    store.acknowledge("u1", "default", [first.id])
    second = asyncio.run(engine.evaluate_daily("u1", "default", TriggerType.time))

    assert first is not None and first.should_message is True
    assert second is None
    assert engine.pacing_store.get_state("u1")["unreplied_streak"] == 1


def test_pacing_resets_on_reply(tmp_dir: Path) -> None:
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", '
        '"importance": 7, "conversation_intent": "direct_question"}',
        pacing_enabled=True,
    )
    engine.settings.proactive_pacing_min_cooldown_seconds = 0
    engine.settings.proactive_pacing_max_cooldown_seconds = 0
    engine.settings.proactive_pacing_max_unreplied = 5
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    first = asyncio.run(engine.evaluate_daily("u1", "default", TriggerType.time))
    assert first is not None
    assert engine.pacing_store.get_state("u1")["unreplied_streak"] == 0

    asyncio.run(engine.record_reply("u1", "default", first.id))

    assert engine.pacing_store.get_state("u1")["unreplied_streak"] == 0


def test_pacing_hard_cap_enters_recoverable_resting(tmp_dir: Path) -> None:
    engine, _session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
        pacing_enabled=True,
    )
    engine.settings.proactive_pacing_min_cooldown_seconds = 0
    engine.settings.proactive_pacing_max_cooldown_seconds = 0
    engine.settings.proactive_pacing_min_unreplied = 2
    engine.settings.proactive_pacing_max_unreplied = 2
    engine.settings.proactive_pacing_max_effective_cooldown_seconds = 86400
    engine.pacing_store.record_settlement("u1", miss_weight=1.0)
    engine.pacing_store.record_settlement("u1", miss_weight=1.0)

    assert engine._is_pacing_blocked("u1", "default") is True
    state = engine.pacing_store.get_state("u1")
    assert state["mode"] == "resting"
    assert datetime.fromisoformat(state["next_probe_at"]) > datetime.now(timezone.utc)


def test_sleep_detection_suppresses_daily(tmp_dir: Path) -> None:
    store = FakeSleepHealthStore(
        2,
        datetime.now(timezone.utc).isoformat(),
    )
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
        health_store=store,
        quiet_hours="",
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    assert engine._is_do_not_disturb("u1", "default") is True
    decision = asyncio.run(engine.evaluate_daily("u1", "default", TriggerType.time))
    assert decision is None


def test_awake_sleep_stage_allows_delivery_during_quiet_hours(tmp_dir: Path) -> None:
    store = FakeSleepHealthStore(
        4,
        datetime.now(timezone.utc).isoformat(),
    )
    engine, session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": true, "insight_key": "tip", "message": "提醒", "importance": 7}',
        health_store=store,
        quiet_hours="00-23",
        cooldown_seconds=0,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    assert engine._is_do_not_disturb("u1", "default") is False
    decision = asyncio.run(engine.evaluate_daily("u1", "default", TriggerType.time))
    assert decision is not None
    assert decision.should_message is True


def test_next_onboarding_guide_returns_first_actionable_guide(tmp_dir: Path) -> None:
    engine, _session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
    )

    # A brand-new user still has the profile name question open, so no button yet.
    assert engine.next_onboarding_guide("u1", "default") is None

    # After name and the no-button capability intro are delivered, the first
    # actionable guide is connecting Xiaomi health.
    engine.profile_store.mark_slot_completed("u1", "default", "name")
    engine.profile_store.mark_guide_filled("u1", "default", "capability_intro")
    guide = engine.next_onboarding_guide("u1", "default")

    assert guide is not None
    slot, actions = guide
    assert slot == "xiaomi_connect"
    assert actions == [{"type": "open_health", "label": "去连接小米手环"}]

    engine.mark_onboarding_guide_delivered("u1", "default", slot)
    assert slot in engine.profile_store.pending_slots("u1", "default")


def test_onboarding_pacing_blocks_after_unanswered_streak(tmp_dir: Path) -> None:
    engine, _session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
        pacing_enabled=True,
        cooldown_seconds=0,
    )
    engine.profile_store.set_phase("u1", "default", "slow")

    for _ in range(10):
        engine.onboarding_pacing_store.record_sent("u1", "default")

    assert engine._is_onboarding_pacing_blocked("u1", "default") is True
    assert (
        asyncio.run(engine.evaluate_onboarding("u1", "default", TriggerType.time))
        is None
    )

    engine.onboarding_pacing_store.record_reply("u1", "default")
    assert engine._is_onboarding_pacing_blocked("u1", "default") is False


def test_guide_action_completed_resets_onboarding_pacing(tmp_dir: Path) -> None:
    engine, _session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
        pacing_enabled=True,
        cooldown_seconds=0,
    )

    for _ in range(10):
        engine.onboarding_pacing_store.record_sent("u1", "default")
    assert engine._is_onboarding_pacing_blocked("u1", "default") is True

    asyncio.run(engine.complete_guide_action("u1", "default", "open_health"))

    assert engine._is_onboarding_pacing_blocked("u1", "default") is False
    assert "xiaomi_connect" in engine.profile_store.completed_slots(
        "u1", "default"
    )


def test_proactive_ledger_survives_restart_without_copying_message_body(
    tmp_dir: Path,
) -> None:
    path = tmp_dir / "decisions.db"
    decision = ProactiveDecision(
        user_id="u1",
        session_id="session-1",
        trigger_type=TriggerType.time,
        should_message=True,
        message="只保存在会话里的正文",
        conversation_intent=ConversationIntent.direct_question,
        actions=[{"type": "open_health", "label": "去看看"}],
    )
    ProactiveStore(path).add(decision)

    restored = ProactiveStore(path).get("u1", "default", decision.id)

    assert restored is not None
    assert restored.session_id == "session-1"
    assert restored.message is None
    assert restored.actions == decision.actions


def test_only_exposed_reply_expected_message_creates_miss(tmp_dir: Path) -> None:
    store = ProactiveStore(tmp_dir / "decisions.db")
    exposed = ProactiveDecision(
        user_id="u1",
        trigger_type=TriggerType.time,
        should_message=True,
        conversation_intent=ConversationIntent.direct_question,
        decided_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    unseen = ProactiveDecision(
        user_id="u1",
        trigger_type=TriggerType.time,
        should_message=True,
        conversation_intent=ConversationIntent.soft_check_in,
        decided_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    store.add(exposed)
    store.add(unseen)
    store.acknowledge("u1", "default", [exposed.id])

    settlements = store.settle_expired(
        "u1", before=datetime.now(timezone.utc)
    )
    weights = {decision.id: weight for decision, weight in settlements}

    assert weights[exposed.id] == 1.0
    assert weights[unseen.id] == 0.0
    assert store.get("u1", "default", exposed.id).engagement_state.value == "seen_no_reply"
    assert store.get("u1", "default", unseen.id).engagement_state.value == "expired_unseen"


def test_resting_probe_schedule_never_becomes_permanent(tmp_dir: Path) -> None:
    pacing = ProactivePacingStore(tmp_dir / "pacing.db")
    now = datetime(2026, 9, 4, tzinfo=timezone.utc)

    pacing.enter_resting("u1", now=now)
    state = pacing.get_state("u1")
    assert state["rest_level"] == 1
    assert datetime.fromisoformat(state["next_probe_at"]) == now + timedelta(hours=12)

    expected = ((2, 24), (3, 72), (4, 24 * 7))
    for level, hours in expected:
        pacing.advance_rest_after_miss("u1", now=now)
        state = pacing.get_state("u1")
        assert state["rest_level"] == level
        assert datetime.fromisoformat(state["next_probe_at"]) == now + timedelta(
            hours=hours
        )


def test_legacy_pacing_migration_and_reset_preserve_lifetime_totals(
    tmp_dir: Path,
) -> None:
    path = tmp_dir / "pacing.db"
    with sqlite3.connect(str(path)) as connection:
        connection.execute(
            """
            CREATE TABLE pacing (
                user_id TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT 'default',
                total_sent INTEGER NOT NULL DEFAULT 0,
                total_replied INTEGER NOT NULL DEFAULT 0,
                unreplied_streak INTEGER NOT NULL DEFAULT 0,
                last_sent_at TEXT,
                last_reply_at TEXT,
                next_daily_due_at TEXT,
                PRIMARY KEY (user_id, agent_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO pacing VALUES (?, 'default', 21, 7, 6, ?, ?, NULL)
            """,
            (
                "u1",
                (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
            ),
        )

    pacing = ProactivePacingStore(path)
    migrated = pacing.get_state("u1")
    assert migrated["total_sent"] == 21
    assert migrated["total_replied"] == 7
    assert migrated["miss_weight"] == 5.0
    assert migrated["opportunities"] == 21

    pacing.reset_control_state("u1", preference="want_more")
    repaired = pacing.get_state("u1")
    assert repaired["total_sent"] == 21
    assert repaired["total_replied"] == 7
    assert repaired["mode"] == "recovery"
    assert repaired["miss_weight"] == 0.5


def test_explicit_want_more_recovers_control_state(tmp_dir: Path) -> None:
    engine, _sessions, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": false, "message": ""}',
        pacing_enabled=True,
    )
    engine.pacing_store.record_settlement("u1", miss_weight=5.0)
    engine.pacing_store.enter_resting("u1")

    asyncio.run(
        engine.record_user_activity(
            "u1", "default", "你今天还没有给我发过消息"
        )
    )

    state = engine.pacing_store.get_state("u1")
    assert state["mode"] == "recovery"
    assert state["miss_weight"] == 0.5
    assert state["initiative_preference"] == "want_more"


def test_restart_safe_semantic_reply_attribution(tmp_dir: Path) -> None:
    engine, session_service, _told, store, _presence = _build_engine(
        tmp_dir,
        '{"replied": true}',
        pacing_enabled=True,
    )
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )
    decision = ProactiveDecision(
        user_id="u1",
        trigger_type=TriggerType.time,
        should_message=True,
        phase=ProactivePhase.daily,
        message="今天过得怎么样？",
        conversation_intent=ConversationIntent.direct_question,
    )
    store.add(decision)
    asyncio.run(engine._deliver_and_persist(decision))

    engine.store = ProactiveStore(tmp_dir / "decisions.db")
    asyncio.run(engine.record_user_activity("u1", "default", "今天还不错"))

    restored = engine.store.get("u1", "default", decision.id)
    assert restored is not None
    assert restored.replied is True
    assert restored.settled_at is not None


def test_onboarding_pending_timeout_becomes_deferred(tmp_dir: Path) -> None:
    profile = ProfileStore(tmp_dir / "profile.db")
    profile.record_ask("u1", "default", "name")

    expired = profile.settle_expired_slots(
        "u1",
        timeout_seconds=1800,
        now=datetime.now(timezone.utc) + timedelta(minutes=31),
    )

    assert expired == {"name"}
    assert profile.pending_slots("u1") == set()
    assert profile.deferred_slots("u1") == {"name"}
    assert profile.settled_count("u1") == 1
    assert profile.completed_count("u1") == 0


def test_onboarding_reconciles_verifiable_real_state(tmp_dir: Path) -> None:
    engine, _sessions, _told, _store, presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
    )
    engine.xiaomi_service = FakeXiaomiService(bound=True)
    presence.register_device("u1", "default", "device-1")
    presence.update_location("u1", "default", 39.9, 116.4)

    engine._reconcile_onboarding_guides("u1", "default")

    completed = engine.profile_store.completed_slots("u1", "default")
    assert {"xiaomi_connect", "proactive_enable", "notification", "location"} <= completed


def test_tick_isolates_one_user_failure(tmp_dir: Path) -> None:
    engine, _sessions, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"should_message": false, "message": ""}',
    )

    async def users() -> list[tuple[str, str]]:
        return [("bad", "default"), ("good", "default")]

    async def evaluate(user_id, agent_id, trigger_type, trigger_source="time"):
        if user_id == "bad":
            raise TimeoutError("isolated")
        return ProactiveDecision(
            user_id=user_id,
            agent_id=agent_id,
            trigger_type=trigger_type,
            should_message=True,
            message="仍然处理后续账号",
        )

    engine.session_service.list_users = users
    engine.evaluate_daily = evaluate

    decisions = asyncio.run(engine.tick())

    assert [decision.user_id for decision in decisions] == ["good"]


def test_next_onboarding_guide_skips_bound_xiaomi(tmp_dir: Path) -> None:
    engine, _session_service, _told, _store, _presence = _build_engine(
        tmp_dir,
        '{"slot": "name", "message": "怎么称呼你？"}',
        onboarding_enabled=True,
        pacing_enabled=True,
        cooldown_seconds=0,
    )
    engine.xiaomi_service = FakeXiaomiService(bound=True)
    engine.profile_store.mark_slot_completed("u1", "default", "name")
    engine.profile_store.mark_guide_filled("u1", "default", "capability_intro")

    guide = engine.next_onboarding_guide("u1", "default")

    assert guide is not None
    slot, actions = guide
    assert slot == "notification"
    assert actions == [{"type": "request_notification", "label": "授权通知"}]
