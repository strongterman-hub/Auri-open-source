from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.health.store import HealthStore
from app.proactive.models import (
    ProactiveCategory,
    ProactiveDecision,
    ProactivePhase,
    TriggerType,
)
from app.reminders.models import Reminder, ReminderKind
from app.reminders.store import ReminderStore
from app.services.timezone_store import resolve_zoneinfo


logger = logging.getLogger("auri.reminders")

_OPERATORS = {
    "<": lambda value, threshold: value < threshold,
    "<=": lambda value, threshold: value <= threshold,
    ">": lambda value, threshold: value > threshold,
    ">=": lambda value, threshold: value >= threshold,
    "==": lambda value, threshold: value == threshold,
}


class ReminderScheduler:
    """Fires due time reminders and satisfied health-condition reminders."""

    def __init__(
        self,
        *,
        reminder_store: ReminderStore,
        session_service,
        health_store: HealthStore,
        delivery,
        timezone_name: str = "Asia/Shanghai",
        timezone_resolver: Callable[[str], str] | None = None,
        tick_seconds: int = 30,
    ) -> None:
        self.reminder_store = reminder_store
        self.session_service = session_service
        self.health_store = health_store
        self.delivery = delivery
        self.timezone_name = timezone_name
        self.timezone_resolver = timezone_resolver
        try:
            self.tz = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            self.tz = ZoneInfo("UTC")
        self.tick_seconds = tick_seconds
        self._task: asyncio.Task | None = None

    async def tick(self) -> list[str]:
        fired: list[str] = []
        now = datetime.now(timezone.utc)
        for user_id, agent_id in await self.session_service.list_users():
            for reminder in self.reminder_store.list_pending(user_id, agent_id):
                if not self._should_fire(reminder, user_id, now):
                    continue
                try:
                    await self._deliver(reminder)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - one bad reminder must not stop the loop
                    logger.exception("reminder fire failed for user %s", user_id)
                    continue
                self._mark_fired(reminder, user_id, now)
                fired.append(reminder.id)
        return fired

    def _should_fire(self, reminder: Reminder, user_id: str, now: datetime) -> bool:
        if reminder.kind is ReminderKind.time:
            return reminder.due_at is not None and reminder.due_at <= now

        if reminder.kind is ReminderKind.event:
            if not self._condition_met(reminder, user_id):
                return False
            if reminder.last_fired_at is not None:
                last = reminder.last_fired_at
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if (now - last).total_seconds() < 86400:
                    return False
            return True

        return False

    def _mark_fired(self, reminder: Reminder, user_id: str, now: datetime) -> None:
        self.reminder_store.mark_fired(
            reminder.id,
            user_id,
            reminder.agent_id,
            now,
            done=reminder.kind is ReminderKind.time,
        )

    def _condition_met(self, reminder: Reminder, user_id: str) -> bool:
        if not reminder.metric_type or not reminder.operator or reminder.threshold is None:
            return False
        tz_name = (
            self.timezone_resolver(user_id)
            if self.timezone_resolver is not None
            else self.timezone_name
        )
        today = datetime.now(resolve_zoneinfo(tz_name)).date().isoformat()
        metrics, _ = self.health_store.get_metrics(user_id, today, today, tz_name)
        values = [
            metric.value1
            for metric in metrics
            if metric.metric_type == reminder.metric_type and metric.value1 is not None
        ]
        if not values:
            return False
        value = max(values)
        operator_fn = _OPERATORS.get(reminder.operator)
        return operator_fn(value, reminder.threshold) if operator_fn else False

    async def _deliver(self, reminder: Reminder) -> None:
        decision = ProactiveDecision(
            user_id=reminder.user_id,
            agent_id=reminder.agent_id,
            trigger_type=(
                TriggerType.time
                if reminder.kind is ReminderKind.time
                else TriggerType.event
            ),
            should_message=True,
            phase=ProactivePhase.daily,
            category=ProactiveCategory.goal_reminder,
            insight_key=f"reminder_{reminder.id}",
            message=reminder.message,
            importance=8,
        )
        await self.delivery.deliver(decision, allow_push=True)

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="auri-reminder-scheduler")

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
                logger.exception("reminder scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)
