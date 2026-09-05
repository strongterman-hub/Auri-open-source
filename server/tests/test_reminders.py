from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.health.store import HealthStore
from app.reminders.models import Reminder, ReminderKind, ReminderStatus
from app.reminders.scheduler import ReminderScheduler
from app.reminders.service import ReminderService, parse_when
from app.reminders.store import ReminderStore
from app.schemas.health import HealthMetricIn


class FakeSessionService:
    def __init__(self, users: list[tuple[str, str]]) -> None:
        self.users = users

    async def list_users(self) -> list[tuple[str, str]]:
        return self.users


class FakeDelivery:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def deliver(self, decision, *, allow_push: bool = True) -> None:
        self.messages.append(decision.message or "")


def test_parse_when() -> None:
    tz = ZoneInfo("Asia/Shanghai")
    assert parse_when("2026-08-24 09:30", tz).isoformat() == "2026-08-24T09:30:00+08:00"
    assert parse_when("in 30 minutes", tz) > datetime.now(tz)
    tomorrow = parse_when("tomorrow 08:00", tz)
    assert tomorrow.hour == 8 and tomorrow.minute == 0
    assert tomorrow.date() == (datetime.now(tz).date() + timedelta(days=1))
    clock = parse_when("08:00", tz)
    assert clock.hour == 8 and clock.minute == 0


def test_reminder_store_roundtrip(tmp_dir: Path) -> None:
    store = ReminderStore(tmp_dir / "reminders.db")
    reminder = Reminder(
        user_id="u1",
        kind=ReminderKind.time,
        message="吃药",
        due_at=datetime.now(timezone.utc),
    )
    store.save(reminder)

    assert store.get(reminder.id, "u1") is not None
    assert store.list_pending("u1")[0].message == "吃药"
    store.mark_fired(
        reminder.id, "u1", "default", datetime.now(timezone.utc), done=True
    )
    assert store.get(reminder.id, "u1").status is ReminderStatus.done
    assert store.delete(reminder.id, "u1") is True
    assert store.get(reminder.id, "u1") is None


def test_reminder_service_create_list_cancel(tmp_dir: Path) -> None:
    service = ReminderService(ReminderStore(tmp_dir / "reminders.db"), "Asia/Shanghai")
    time_reminder = service.create_time("u1", "default", "吃药", "tomorrow 08:00")
    event_reminder = service.create_event(
        "u1", "default", "多走动", "STEPS", "<", 3000
    )

    assert time_reminder.kind is ReminderKind.time
    assert time_reminder.due_at is not None
    assert event_reminder.kind is ReminderKind.event
    assert len(service.list("u1")) == 2
    assert service.cancel(time_reminder.id, "u1") is True
    assert len(service.list("u1")) == 1


def test_scheduler_fires_due_time_reminder(tmp_dir: Path) -> None:
    store = ReminderStore(tmp_dir / "reminders.db")
    reminder = Reminder(
        user_id="u1",
        kind=ReminderKind.time,
        message="时间到",
        due_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    store.save(reminder)
    delivery = FakeDelivery()
    scheduler = ReminderScheduler(
        reminder_store=store,
        session_service=FakeSessionService([("u1", "default")]),
        health_store=HealthStore(tmp_dir / "health"),
        delivery=delivery,
        timezone_name="Asia/Shanghai",
    )

    fired = asyncio.run(scheduler.tick())

    assert fired == [reminder.id]
    assert delivery.messages == ["时间到"]
    assert store.get(reminder.id, "u1").status is ReminderStatus.done


def test_scheduler_fires_event_reminder_once_per_day(tmp_dir: Path) -> None:
    store = ReminderStore(tmp_dir / "reminders.db")
    reminder = Reminder(
        user_id="u1",
        kind=ReminderKind.event,
        message="步数偏低",
        metric_type="STEPS",
        operator="<",
        threshold=5000,
    )
    store.save(reminder)
    health_store = HealthStore(tmp_dir / "health")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    health_store.sync(
        user_id="u1",
        from_day=today,
        to_day=today,
        metric_types=["STEPS"],
        metrics=[HealthMetricIn(metric_type="STEPS", day=today, value1=3000)],
        samples=[],
    )
    delivery = FakeDelivery()
    scheduler = ReminderScheduler(
        reminder_store=store,
        session_service=FakeSessionService([("u1", "default")]),
        health_store=health_store,
        delivery=delivery,
        timezone_name="Asia/Shanghai",
    )

    assert asyncio.run(scheduler.tick()) == [reminder.id]
    assert delivery.messages == ["步数偏低"]
    assert asyncio.run(scheduler.tick()) == []
