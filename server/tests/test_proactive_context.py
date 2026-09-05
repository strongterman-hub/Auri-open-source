from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.memory.models import MemoryScope
from app.observation.models import ObservationSource
from app.observation.store import ObservationStore
from app.proactive.context import (
    Interruptibility,
    ProactiveAuditLogger,
    ProactiveContextBuilder,
    SignalFreshness,
    SituationSignal,
    SituationSnapshot,
)
from app.proactive.pacing import ProactivePacingStore
from app.reminders.models import Reminder, ReminderKind
from app.reminders.store import ReminderStore
from app.schemas.health import HealthMetricOut, HealthSampleOut
from app.services.device_store import DeviceStore
from app.services.observation_service import ObservationService
from app.services.presence_service import PresenceService
from app.services.session_service import SessionService
from app.session.models import SessionCreate
from app.session.store import FileSessionStore
from app.todos import Todo, TodoStore


class FakeEventMemory:
    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = events or []

    async def search(self, *_args, **_kwargs) -> list[dict]:
        return self.events


class FakeWeather:
    latitude = 39.9
    longitude = 116.4

    async def fetch_current(self, latitude: float, longitude: float) -> dict:
        return {
            "latitude": latitude,
            "longitude": longitude,
            "temperature_2m": 25,
            "weather_code": 1,
        }


class SlowWeather(FakeWeather):
    async def fetch_current(self, latitude: float, longitude: float) -> dict:
        await asyncio.sleep(0.1)
        return await super().fetch_current(latitude, longitude)


class FakeHealthStore:
    def __init__(self, samples: dict[str, HealthSampleOut] | None = None) -> None:
        self.samples = samples or {}

    def latest_sample(self, _user_id: str, sample_type: str):
        return self.samples.get(sample_type)

    def get_metrics(self, _user_id: str, day: str, _to_day: str, _tz: str):
        updated_at = int(datetime.now(timezone.utc).timestamp() * 1000)
        return [
            HealthMetricOut(
                metric_type="STEPS",
                day=day,
                value1=4321,
                updated_at=updated_at,
            )
        ], []


def _builder(
    tmp_dir: Path,
    *,
    health_store=None,
    weather_service=None,
    event_memory=None,
    weather_timeout_seconds: float = 0.1,
) -> tuple[
    ProactiveContextBuilder,
    SessionService,
    ObservationService,
    PresenceService,
    ReminderStore,
    TodoStore,
    ProactivePacingStore,
]:
    sessions = SessionService(FileSessionStore(tmp_dir / "sessions"))
    observations = ObservationService(ObservationStore(tmp_dir / "observations.db"))
    presence = PresenceService(DeviceStore(tmp_dir / "devices.db"))
    reminders = ReminderStore(tmp_dir / "reminders.db")
    todos = TodoStore(tmp_dir / "todos.db")
    pacing = ProactivePacingStore(tmp_dir / "pacing.db")
    builder = ProactiveContextBuilder(
        session_service=sessions,
        observation_service=observations,
        presence=presence,
        reminder_store=reminders,
        todo_store=todos,
        pacing_store=pacing,
        health_store=health_store,
        weather_service=weather_service,
        event_memory_service=event_memory,
        timezone_resolver=lambda _user_id: "Asia/Shanghai",
        presence_ttl_seconds=180,
        weather_timeout_seconds=weather_timeout_seconds,
    )
    return builder, sessions, observations, presence, reminders, todos, pacing


def test_context_builder_collects_source_balanced_timestamped_snapshot(
    tmp_dir: Path,
) -> None:
    now = datetime.now(timezone.utc)
    event = {
        "id": "event-1",
        "event_key": "moved_home",
        "kind": "life_event",
        "status": "completed",
        "occurred_start": (now - timedelta(days=1)).isoformat(),
        "updated_at": now.isoformat(),
        "core_summary": "用户昨天搬进新家",
        "confidence": 0.9,
    }
    heart_rate = HealthSampleOut(
        metric_type="HEART_RATE",
        day=now.date().isoformat(),
        bucket_start=(now - timedelta(minutes=10)).isoformat(),
        bucket_end=(now - timedelta(minutes=9)).isoformat(),
        value1=68,
        quality=0.9,
    )
    (
        builder,
        sessions,
        observations,
        presence,
        reminders,
        todos,
        pacing,
    ) = _builder(
        tmp_dir,
        health_store=FakeHealthStore({"HEART_RATE": heart_rate}),
        weather_service=FakeWeather(),
        event_memory=FakeEventMemory([event]),
    )
    session = asyncio.run(
        sessions.create(SessionCreate(user_id="u1", agent_id="default"))
    )
    session.messages.append(
        {
            "id": "message-1",
            "role": "user",
            "content": "我正在整理房间",
            "timestamp": (now - timedelta(minutes=2)).isoformat(),
        }
    )
    asyncio.run(sessions.save(session))

    device_store = presence.device_store
    assert device_store is not None
    device_store.update_location(
        "u1", "default", 39.90, 116.40, now - timedelta(minutes=5)
    )
    device_store.update_location(
        "u1", "default", 39.91, 116.41, now - timedelta(minutes=1)
    )
    presence.heartbeat("u1")
    pacing.record_sent("u1")
    reminders.save(
        Reminder(
            user_id="u1",
            kind=ReminderKind.time,
            message="晚上买牛奶",
            due_at=now + timedelta(hours=2),
        )
    )
    todos.add(Todo(user_id="u1", title="整理书架"))

    for index in range(12):
        observations.ingest_event(
            "u1",
            "default",
            ObservationSource.conversation,
            "message",
            {"index": index},
            now - timedelta(seconds=index),
        )
    observations.ingest_event(
        "u1",
        "default",
        ObservationSource.health,
        "sync",
        {"sample_count": 1},
        now - timedelta(minutes=3),
    )
    observations.ingest_event(
        "u1",
        "default",
        ObservationSource.weather,
        "change",
        {"temperature_2m": 25},
        now - timedelta(minutes=3),
    )

    snapshot = asyncio.run(
        builder.build(MemoryScope(user_id="u1"), now=now)
    )
    sources = {signal.source for signal in snapshot.signals}
    assert {
        "time",
        "conversation",
        "event_memory",
        "gps",
        "weather",
        "health",
        "reminder",
        "todo",
        "presence",
        "pacing",
        "observation",
    } <= sources
    observation_kinds = {
        signal.kind for signal in snapshot.signals if signal.source == "observation"
    }
    assert "health/sync" in observation_kinds
    assert "weather/change" in observation_kinds
    assert snapshot.signal("gps_latest").freshness is SignalFreshness.fresh
    assert snapshot.signal("weather_current").value["is_user_current_location"] is True
    assert snapshot.interruptibility is Interruptibility.low
    assert "gps_latest" in snapshot.interruptibility_evidence


def test_context_builder_blocks_fresh_sleep_and_not_future_meeting(
    tmp_dir: Path,
) -> None:
    now = datetime.now(timezone.utc)
    sleeping = HealthSampleOut(
        metric_type="SLEEP_STAGE",
        day=now.date().isoformat(),
        bucket_start=(now - timedelta(minutes=4)).isoformat(),
        bucket_end=(now - timedelta(minutes=3)).isoformat(),
        value1=2,
        quality=0.9,
    )
    future_meeting = {
        "id": "future-meeting",
        "event_key": "meeting_later",
        "kind": "meeting",
        "status": "planned",
        "occurred_start": (now + timedelta(hours=2)).isoformat(),
        "occurred_end": (now + timedelta(hours=3)).isoformat(),
        "current_until": (now + timedelta(hours=3)).isoformat(),
        "core_summary": "下午正在开会",
        "confidence": 0.9,
    }
    builder, *_ = _builder(
        tmp_dir,
        health_store=FakeHealthStore({"SLEEP_STAGE": sleeping}),
        event_memory=FakeEventMemory([future_meeting]),
    )

    snapshot = asyncio.run(
        builder.build(MemoryScope(user_id="u1"), now=now)
    )
    assert snapshot.interruptibility is Interruptibility.do_not_interrupt
    assert "health_sleep_stage_latest" in snapshot.interruptibility_evidence
    assert "event_future-meeting" not in snapshot.interruptibility_evidence
    assert snapshot.signal("event_future-meeting").value["active_now"] is False


def test_expired_gps_does_not_masquerade_as_current_weather_location(
    tmp_dir: Path,
) -> None:
    now = datetime.now(timezone.utc)
    builder, _sessions, _observations, presence, *_ = _builder(
        tmp_dir,
        weather_service=FakeWeather(),
    )
    device_store = presence.device_store
    assert device_store is not None
    device_store.update_location(
        "u1",
        "default",
        31.2,
        121.5,
        now - timedelta(hours=3),
    )

    snapshot = asyncio.run(
        builder.build(MemoryScope(user_id="u1"), now=now)
    )

    gps = snapshot.signal("gps_latest")
    weather = snapshot.signal("weather_current")
    assert gps is not None and gps.freshness is SignalFreshness.expired
    assert weather is not None
    assert weather.value["location_source"] == "configured_fallback"
    assert weather.value["is_user_current_location"] is False


def test_conversation_context_is_scoped_to_agent(tmp_dir: Path) -> None:
    now = datetime.now(timezone.utc)
    builder, sessions, *_ = _builder(tmp_dir)
    other = asyncio.run(
        sessions.create(SessionCreate(user_id="u1", agent_id="other"))
    )
    other.messages.append(
        {
            "id": "private-other-agent",
            "role": "user",
            "content": "我在忙，别打扰",
            "timestamp": now.isoformat(),
        }
    )
    asyncio.run(sessions.save(other))

    snapshot = asyncio.run(
        builder.build(MemoryScope(user_id="u1", agent_id="default"), now=now)
    )

    assert not any(signal.source == "conversation" for signal in snapshot.signals)
    assert snapshot.interruptibility is Interruptibility.normal


def test_context_builder_isolates_weather_timeout(tmp_dir: Path) -> None:
    builder, *_ = _builder(
        tmp_dir,
        weather_service=SlowWeather(),
        weather_timeout_seconds=0.01,
    )

    snapshot = asyncio.run(builder.build(MemoryScope(user_id="u1")))

    assert snapshot.signal("time_now") is not None
    assert snapshot.availability["weather"] == "error"


def test_signal_limit_round_robins_across_sources(tmp_dir: Path) -> None:
    builder, *_ = _builder(tmp_dir)
    builder.max_signals = 3
    signals = [
        SituationSignal(
            id=f"conversation_{index}",
            source="conversation",
            kind="message",
            value={},
        )
        for index in range(8)
    ]
    signals.extend(
        [
            SituationSignal(id="health_1", source="health", kind="heart_rate"),
            SituationSignal(id="gps_1", source="gps", kind="location"),
        ]
    )

    selected = builder._limit_signals(signals)

    assert [signal.source for signal in selected] == [
        "conversation",
        "health",
        "gps",
    ]


def test_audit_logger_redacts_location_and_conversation_values(tmp_dir: Path) -> None:
    now = datetime.now(timezone.utc)
    snapshot = SituationSnapshot(
        user_id="u1",
        generated_at=now,
        timezone="Asia/Shanghai",
        signals=[
            SituationSignal(
                id="gps_latest",
                source="gps",
                kind="last_reported_location",
                value={
                    "latitude": 39.9,
                    "longitude": 116.4,
                    "movement": "stationary",
                },
                observed_at=now,
                freshness=SignalFreshness.fresh,
            ),
            SituationSignal(
                id="conversation_1",
                source="conversation",
                kind="message",
                value={"role": "user", "text": "这是私密对话"},
                observed_at=now,
                freshness=SignalFreshness.fresh,
            ),
        ],
    )
    path = tmp_dir / "proactive_context.jsonl"
    ProactiveAuditLogger(path).write(
        snapshot,
        {"should_message": False, "message": "不应写入审计的消息正文"},
    )

    record = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(record, ensure_ascii=False)
    assert "39.9" not in serialized
    assert "116.4" not in serialized
    assert "这是私密对话" not in serialized
    assert "不应写入审计的消息正文" not in serialized
    assert "stationary" in serialized
