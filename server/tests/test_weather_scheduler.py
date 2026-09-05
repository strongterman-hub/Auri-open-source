from __future__ import annotations

import asyncio

from app.observation.models import Observation, ObservationSource
from app.proactive.models import TriggerType
from app.services.weather_scheduler import WeatherScheduler


class FakeWeatherService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def update(self, user_id: str, agent_id: str) -> Observation | None:
        self.calls.append((user_id, agent_id))
        return Observation(
            user_id=user_id,
            agent_id=agent_id,
            source=ObservationSource.weather,
            kind="weather_changed",
            payload={"weather_code": 1},
        )


class FakeSessionService:
    async def list_users(self) -> list[tuple[str, str]]:
        return [("u1", "default"), ("u2", "default")]


class FakeEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, TriggerType, str]] = []

    async def evaluate_daily(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
        trigger_source: str = "time",
    ) -> None:
        self.calls.append((user_id, agent_id, trigger_type, trigger_source))


def test_weather_scheduler_triggers_event_for_changed_users() -> None:
    weather = FakeWeatherService()
    engine = FakeEngine()
    scheduler = WeatherScheduler(
        weather_service=weather,
        session_service=FakeSessionService(),
        proactive_engine=engine,
        tick_seconds=60,
    )

    changed = asyncio.run(scheduler.tick())

    assert changed == [("u1", "default"), ("u2", "default")]
    assert engine.calls == [
        ("u1", "default", TriggerType.event, "weather"),
        ("u2", "default", TriggerType.event, "weather"),
    ]
