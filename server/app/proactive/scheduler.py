from __future__ import annotations

import asyncio
import logging

from app.proactive.engine import ProactiveEngine


logger = logging.getLogger("auri.proactive")


class ProactiveScheduler:
    """Background loop that invokes the proactive engine on a fixed cadence."""

    def __init__(
        self,
        engine: ProactiveEngine,
        tick_seconds: int,
        onboarding_tick_seconds: int = 60,
        onboarding_slow_tick_seconds: int = 3600,
    ) -> None:
        self.engine = engine
        self.tick_seconds = tick_seconds
        self.onboarding_tick_seconds = onboarding_tick_seconds
        self.onboarding_slow_tick_seconds = onboarding_slow_tick_seconds
        self._task: asyncio.Task | None = None
        self._onboarding_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="auri-proactive-scheduler")
        if self._onboarding_task is None:
            self._onboarding_task = asyncio.create_task(
                self._run_onboarding(), name="auri-proactive-onboarding-scheduler"
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._onboarding_task is not None:
            self._onboarding_task.cancel()
            try:
                await self._onboarding_task
            except asyncio.CancelledError:
                pass
            self._onboarding_task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.engine.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - scheduler must survive a bad tick
                logger.exception("proactive scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)

    async def _run_onboarding(self) -> None:
        while True:
            try:
                await self.engine.tick_onboarding()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - scheduler must survive a bad tick
                logger.exception("proactive onboarding scheduler tick failed")
            await asyncio.sleep(self.onboarding_slow_tick_seconds)
