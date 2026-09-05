from __future__ import annotations

import asyncio
import logging

from app.proactive.models import TriggerType
from app.services.session_service import SessionService
from app.services.weather_service import WeatherService


logger = logging.getLogger("auri.weather_sync")


class WeatherScheduler:
    """Periodically updates the weather observation source for active users.

    When a change is detected, the produced observation is handed to the
    proactive engine as an event trigger. The scheduler survives individual
    fetch or evaluation failures so one bad user cannot stop the loop.
    """

    def __init__(
        self,
        *,
        weather_service: WeatherService,
        session_service: SessionService,
        proactive_engine=None,
        tick_seconds: int,
    ) -> None:
        self.weather_service = weather_service
        self.session_service = session_service
        self.proactive_engine = proactive_engine
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None

    async def tick(self) -> list[tuple[str, str]]:
        changed: list[tuple[str, str]] = []
        for user_id, agent_id in await self.session_service.list_users():
            try:
                observation = await self.weather_service.update(user_id, agent_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad fetch must not stop the loop
                logger.exception("weather update failed for user %s", user_id)
                continue

            if observation is None:
                continue

            changed.append((user_id, agent_id))
            if self.proactive_engine is not None:
                try:
                    await self.proactive_engine.evaluate_daily(
                        user_id,
                        agent_id,
                        TriggerType.event,
                        trigger_source="weather",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - evaluation must not stop the loop
                    logger.exception("proactive evaluation failed for user %s", user_id)
        return changed

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="auri-weather-scheduler")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - scheduler must survive a bad tick
                logger.exception("weather scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)
