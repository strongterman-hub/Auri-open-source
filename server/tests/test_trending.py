from __future__ import annotations

import asyncio
from pathlib import Path

from app.agent.llm import LLMClient, LLMResponse
from app.config import Settings
from app.memory.file_store import FileMemoryStore
from app.memory.intents import IntentStore
from app.memory.told import ToldStore
from app.observation.store import ObservationStore
from app.proactive.delivery import ProactiveDelivery
from app.proactive.engine import ProactiveEngine
from app.proactive.models import ProactiveCategory, TriggerType
from app.proactive.pacing import ProactivePacingStore
from app.proactive.preferences import PreferenceStore
from app.proactive.profile import ProfileStore
from app.proactive.push import NullPushSender
from app.proactive.settings import ProactiveSettingsStore
from app.proactive.store import ProactiveStore
from app.services.memory_service import MemoryService
from app.services.observation_service import ObservationService
from app.services.presence_service import PresenceService
from app.services.session_service import SessionService
from app.services.trending_service import TrendingService
from app.session.models import SessionCreate
from app.session.store import FileSessionStore
from app.web.client import SearchResult


class TrendingDecisionLLM(LLMClient):
    async def complete(self, messages, tools=None, max_tokens=None):
        return LLMResponse(
            content=(
                '{"category": "trending", "should_message": true, '
                '"insight_key": "t1", "message": "今天有条热点：真实事件。", '
                '"push_message": "今日热点", "importance": 7}'
            )
        )

    async def stream(self, messages, tools=None, max_tokens=None):
        raise NotImplementedError


class FakeSearchClient:
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        self.calls += 1
        return [
            SearchResult(title="热点", url="https://example.com/1", snippet="摘要"),
            SearchResult(title="热点二", url="https://example.com/2", snippet="摘要二"),
        ]


def _build_engine(
    tmp_dir: Path,
) -> tuple[ProactiveEngine, SessionService, ToldStore]:
    settings = Settings(
        data_dir=tmp_dir,
        proactive_enabled=True,
        proactive_cooldown_seconds=0,
        proactive_quiet_hours="",
        trending_enabled=True,
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
    trending_service = TrendingService(
        FakeSearchClient(),
        "今日热点",
        max_results=6,
        cache_seconds=1000,
    )
    engine = ProactiveEngine(
        llm=TrendingDecisionLLM(),
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
        trending_service=trending_service,
    )
    return engine, session_service, told_store


def test_trending_service_caches_results() -> None:
    client = FakeSearchClient()
    service = TrendingService(client, "今日热点", max_results=6, cache_seconds=1000)

    first = asyncio.run(service.items())
    second = asyncio.run(service.items())

    assert len(first) == 2
    assert second == first
    assert client.calls == 1


def test_trending_is_daily_candidate_and_delivers(tmp_dir: Path) -> None:
    engine, session_service, told_store = _build_engine(tmp_dir)
    asyncio.run(
        session_service.create(SessionCreate(user_id="u1", agent_id="default"))
    )

    decision = asyncio.run(
        engine.evaluate_daily("u1", "default", TriggerType.time, "time")
    )

    assert decision is not None
    assert decision.should_message is True
    assert decision.category is ProactiveCategory.trending
    assert told_store.has_told(decision.insight_key, "u1") is True
