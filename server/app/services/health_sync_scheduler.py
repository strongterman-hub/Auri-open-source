from __future__ import annotations

import asyncio
import logging

from app.auth.store import AuthStore
from app.integrations.xiaomi.credential_store import CredentialStore
from app.integrations.xiaomi.service import XiaomiService
from app.proactive.models import TriggerType


logger = logging.getLogger("auri.health_sync")


class HealthSyncScheduler:
    """Periodically syncs health data for every Xiaomi-bound user."""

    def __init__(
        self,
        *,
        auth_store: AuthStore,
        credential_store: CredentialStore,
        xiaomi_service: XiaomiService,
        tick_seconds: int,
        proactive_engine=None,
    ) -> None:
        self.auth_store = auth_store
        self.credential_store = credential_store
        self.xiaomi_service = xiaomi_service
        self.tick_seconds = tick_seconds
        self.proactive_engine = proactive_engine
        self._task: asyncio.Task | None = None

    async def tick(self) -> list[str]:
        synced: list[str] = []
        for user_id in self.auth_store.list_user_ids():
            if not self.credential_store.exists(user_id):
                continue
            try:
                await self.xiaomi_service.sync(user_id)
                synced.append(user_id)
                if self.proactive_engine is not None:
                    await self.proactive_engine.evaluate_daily(
                        user_id,
                        "default",
                        TriggerType.event,
                        trigger_source="health",
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad sync must not stop the loop
                logger.exception("health sync failed for user %s", user_id)
        return synced

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="auri-health-sync")

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
                logger.exception("health sync scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)
