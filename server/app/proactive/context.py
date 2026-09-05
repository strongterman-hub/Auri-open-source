from __future__ import annotations

import asyncio
import json
import math
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.memory.events import EventStatus
from app.memory.models import MemoryScope
from app.observation.models import ObservationSource
from app.reminders.store import ReminderStore
from app.services.event_memory_service import EventMemoryService
from app.services.observation_service import ObservationService
from app.services.presence_service import LocationReading, PresenceService
from app.services.session_service import SessionService
from app.services.timezone_store import resolve_zoneinfo
from app.services.weather_service import WeatherService
from app.todos import TodoStatus, TodoStore


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, str) and value:
        try:
            return _aware(datetime.fromisoformat(value))
        except ValueError:
            return None
    if isinstance(value, (int, float)) and value > 0:
        seconds = float(value) / 1000.0 if float(value) > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    return None


class SignalFreshness(str, Enum):
    fresh = "fresh"
    stale = "stale"
    expired = "expired"
    unknown = "unknown"


class Interruptibility(str, Enum):
    high = "high"
    normal = "normal"
    low = "low"
    do_not_interrupt = "do_not_interrupt"


class SituationSignal(BaseModel):
    id: str
    source: str
    kind: str
    value: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None
    age_seconds: float | None = None
    freshness: SignalFreshness = SignalFreshness.unknown
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class SituationSnapshot(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    agent_id: str = "default"
    generated_at: datetime
    timezone: str
    trigger_source: str = "time"
    signals: list[SituationSignal] = Field(default_factory=list)
    availability: dict[str, str] = Field(default_factory=dict)
    interruptibility: Interruptibility = Interruptibility.normal
    interruptibility_reasons: list[str] = Field(default_factory=list)
    interruptibility_evidence: list[str] = Field(default_factory=list)

    def evidence_ids(self) -> set[str]:
        return {signal.id for signal in self.signals}

    def signal(self, evidence_id: str) -> SituationSignal | None:
        return next((item for item in self.signals if item.id == evidence_id), None)

    def context_text(self) -> str:
        lines = [
            f"snapshot_id={self.id}",
            f"generated_at={self.generated_at.isoformat()}",
            f"timezone={self.timezone}",
            f"trigger_source={self.trigger_source}",
            f"availability={json.dumps(self.availability, ensure_ascii=False)}",
            f"interruptibility={self.interruptibility.value}",
            "interruptibility_reasons="
            + (" | ".join(self.interruptibility_reasons) or "none"),
            "Signals (each bracketed id is a valid evidence_ref):",
        ]
        for signal in self.signals:
            observed = signal.observed_at.isoformat() if signal.observed_at else "unknown"
            age = (
                str(round(signal.age_seconds))
                if signal.age_seconds is not None
                else "unknown"
            )
            lines.append(
                f"- [{signal.id}] {signal.source}/{signal.kind} "
                f"freshness={signal.freshness.value} observed_at={observed} "
                f"age_seconds={age} confidence={signal.confidence:.2f} "
                f"value={json.dumps(signal.value, ensure_ascii=False)}"
            )
        return "\n".join(lines)

    def audit_dict(self) -> dict[str, Any]:
        signal_metadata: list[dict[str, Any]] = []
        for signal in self.signals:
            item = {
                "id": signal.id,
                "source": signal.source,
                "kind": signal.kind,
                "observed_at": (
                    signal.observed_at.isoformat() if signal.observed_at else None
                ),
                "age_seconds": signal.age_seconds,
                "freshness": signal.freshness.value,
                "confidence": signal.confidence,
            }
            if signal.source == "gps":
                item["value"] = {
                    key: signal.value.get(key)
                    for key in (
                        "movement",
                        "distance_m",
                        "speed_mps",
                        "location_source",
                    )
                    if key in signal.value
                }
            signal_metadata.append(item)
        return {
            "id": self.id,
            "user_id": self.user_id,
            "agent_id": self.agent_id,
            "generated_at": self.generated_at.isoformat(),
            "timezone": self.timezone,
            "trigger_source": self.trigger_source,
            "availability": self.availability,
            "interruptibility": self.interruptibility.value,
            "interruptibility_reasons": self.interruptibility_reasons,
            "interruptibility_evidence": self.interruptibility_evidence,
            "signals": signal_metadata,
        }


class ProactiveAuditLogger:
    """Append privacy-trimmed situation and decision evidence for debugging."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(
        self,
        snapshot: SituationSnapshot,
        decision: Any,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        raw_decision = (
            decision.model_dump(mode="json")
            if hasattr(decision, "model_dump")
            else dict(decision or {})
        )
        allowed_decision_fields = {
            "id",
            "user_id",
            "agent_id",
            "trigger_type",
            "should_message",
            "phase",
            "category",
            "insight_key",
            "context_snapshot_id",
            "situation_confidence",
            "evidence_refs",
            "decision_reason",
            "silence_reason",
            "decided_at",
            "delivery_channel",
        }
        decision_payload = {
            key: value
            for key, value in raw_decision.items()
            if key in allowed_decision_fields
        }
        record = {
            "snapshot": snapshot.audit_dict(),
            "decision": decision_payload,
            "tool_calls": tool_calls or [],
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class ProactiveGateLogger:
    """Append a privacy-minimal outcome for every deterministic gate check."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(
        self,
        *,
        user_id: str,
        agent_id: str,
        trigger_source: str,
        outcome: str,
        reason: str,
        pacing_state: dict[str, Any] | None = None,
        outstanding: tuple[int, int] | None = None,
    ) -> None:
        state = pacing_state or {}
        record = {
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "user_id": user_id,
            "agent_id": agent_id,
            "trigger_source": trigger_source,
            "outcome": outcome,
            "reason": reason,
            "pacing": {
                key: state.get(key)
                for key in (
                    "mode",
                    "miss_weight",
                    "rest_level",
                    "next_probe_at",
                    "next_daily_due_at",
                )
            },
            "outstanding": {
                "direct": outstanding[0] if outstanding else 0,
                "interactive": outstanding[1] if outstanding else 0,
            },
        }
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class ProactiveContextBuilder:
    """Build a source-balanced, freshness-aware snapshot before LLM decisions."""

    def __init__(
        self,
        *,
        session_service: SessionService,
        observation_service: ObservationService,
        presence: PresenceService,
        reminder_store: ReminderStore,
        todo_store: TodoStore,
        pacing_store: Any,
        health_store: Any = None,
        weather_service: WeatherService | None = None,
        event_memory_service: EventMemoryService | None = None,
        timezone_resolver: Callable[[str], str] | None = None,
        presence_ttl_seconds: int = 5,
        gps_fresh_seconds: int = 900,
        gps_stale_seconds: int = 7200,
        conversation_fresh_seconds: int = 1800,
        conversation_stale_seconds: int = 21600,
        weather_fresh_seconds: int = 1800,
        weather_stale_seconds: int = 7200,
        health_fresh_seconds: int = 1800,
        health_stale_seconds: int = 21600,
        weather_timeout_seconds: float = 3.0,
        max_signals: int = 48,
        schedule_service=None,
    ) -> None:
        self.session_service = session_service
        self.schedule_service = schedule_service
        self.observation_service = observation_service
        self.presence = presence
        self.reminder_store = reminder_store
        self.todo_store = todo_store
        self.pacing_store = pacing_store
        self.health_store = health_store
        self.weather_service = weather_service
        self.event_memory_service = event_memory_service
        self.timezone_resolver = timezone_resolver
        self.presence_ttl_seconds = presence_ttl_seconds
        self.gps_fresh_seconds = gps_fresh_seconds
        self.gps_stale_seconds = gps_stale_seconds
        self.conversation_fresh_seconds = conversation_fresh_seconds
        self.conversation_stale_seconds = conversation_stale_seconds
        self.weather_fresh_seconds = weather_fresh_seconds
        self.weather_stale_seconds = weather_stale_seconds
        self.health_fresh_seconds = health_fresh_seconds
        self.health_stale_seconds = health_stale_seconds
        self.weather_timeout_seconds = weather_timeout_seconds
        self.max_signals = max(8, max_signals)
        self._weather_cache: dict[str, tuple[datetime, float, float, dict[str, Any], str]] = {}

    def _timezone(self, user_id: str) -> str:
        if self.timezone_resolver is not None:
            resolved = self.timezone_resolver(user_id)
            if resolved:
                return resolved
        return "Asia/Shanghai"

    @staticmethod
    def _freshness(
        observed_at: datetime | None,
        now: datetime,
        fresh_seconds: float,
        stale_seconds: float,
    ) -> tuple[float | None, SignalFreshness]:
        if observed_at is None:
            return None, SignalFreshness.unknown
        age = max(0.0, (_aware(now) - _aware(observed_at)).total_seconds())
        if age <= fresh_seconds:
            return age, SignalFreshness.fresh
        if age <= stale_seconds:
            return age, SignalFreshness.stale
        return age, SignalFreshness.expired

    def _signal(
        self,
        *,
        evidence_id: str,
        source: str,
        kind: str,
        value: dict[str, Any],
        observed_at: datetime | None,
        now: datetime,
        fresh_seconds: float,
        stale_seconds: float,
        confidence: float = 1.0,
        freshness: SignalFreshness | None = None,
    ) -> SituationSignal:
        age, calculated = self._freshness(
            observed_at,
            now,
            fresh_seconds,
            stale_seconds,
        )
        return SituationSignal(
            id=evidence_id,
            source=source,
            kind=kind,
            value=value,
            observed_at=observed_at,
            age_seconds=age,
            freshness=freshness or calculated,
            confidence=confidence,
        )

    async def build(
        self,
        scope: MemoryScope,
        *,
        trigger_source: str = "time",
        now: datetime | None = None,
    ) -> SituationSnapshot:
        timezone_name = self._timezone(scope.user_id)
        current = _aware(now or datetime.now(resolve_zoneinfo(timezone_name)))
        local_now = current.astimezone(resolve_zoneinfo(timezone_name))
        time_signal = SituationSignal(
            id="time_now",
            source="time",
            kind="current_local_time",
            value={
                "iso": local_now.isoformat(),
                "weekday": local_now.strftime("%A"),
                "hour": local_now.hour,
                "timezone": timezone_name,
            },
            observed_at=current,
            age_seconds=0,
            freshness=SignalFreshness.fresh,
            confidence=1.0,
        )
        tasks: dict[str, Any] = {
            "conversation": self._conversation_signals(scope, current),
            "event_memory": self._event_signals(scope, current),
            "gps": asyncio.to_thread(self._gps_signals, scope, current),
            "weather": self._weather_signals(scope, current),
            "health": asyncio.to_thread(
                self._health_signals,
                scope,
                current,
                timezone_name,
            ),
            "reminders": asyncio.to_thread(self._reminder_signals, scope, current),
            "schedule": asyncio.to_thread(self._schedule_signals, scope, current),
            "todos": asyncio.to_thread(self._todo_signals, scope, current),
            "presence": asyncio.to_thread(self._presence_signals, scope, current),
            "pacing": asyncio.to_thread(self._pacing_signals, scope, current),
            "observations": asyncio.to_thread(
                self._observation_signals,
                scope,
                current,
            ),
        }
        names = list(tasks)
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        signals = [time_signal]
        availability = {"time": "available"}
        for name, result in zip(names, results):
            if isinstance(result, BaseException):
                availability[name] = "error"
                continue
            source_signals = list(result)
            signals.extend(source_signals)
            availability[name] = self._availability(source_signals)

        snapshot = SituationSnapshot(
            user_id=scope.user_id,
            agent_id=scope.agent_id,
            generated_at=current,
            timezone=timezone_name,
            trigger_source=trigger_source,
            signals=self._limit_signals(signals),
            availability=availability,
        )
        self._apply_interruptibility(snapshot)
        return snapshot

    def _limit_signals(
        self,
        signals: list[SituationSignal],
    ) -> list[SituationSignal]:
        """Round-robin sources so one verbose source cannot crowd out others."""
        buckets: dict[str, list[SituationSignal]] = {}
        for signal in signals:
            buckets.setdefault(signal.source, []).append(signal)
        selected: list[SituationSignal] = []
        while len(selected) < self.max_signals:
            added = False
            for bucket in buckets.values():
                if bucket and len(selected) < self.max_signals:
                    selected.append(bucket.pop(0))
                    added = True
            if not added:
                break
        return selected

    @staticmethod
    def _availability(signals: list[SituationSignal]) -> str:
        if not signals:
            return "unavailable"
        freshness = {signal.freshness for signal in signals}
        if SignalFreshness.fresh in freshness:
            return "available"
        if SignalFreshness.stale in freshness:
            return "stale"
        if freshness == {SignalFreshness.expired}:
            return "expired"
        return "unknown"

    async def _conversation_signals(
        self,
        scope: MemoryScope,
        now: datetime,
    ) -> list[SituationSignal]:
        sessions = await self.session_service.list_by_user(scope.user_id)
        active = [
            item
            for item in sessions
            if item.agent_id == scope.agent_id and item.end_reason is None
        ]
        if not active:
            return []
        session = max(active, key=lambda item: item.updated_at)
        signals: list[SituationSignal] = []
        for index, message in enumerate(session.messages[-8:]):
            text = self._message_text(message.get("content"))
            if not text:
                continue
            observed = _parse_time(message.get("timestamp"))
            message_id = str(message.get("id") or f"tail_{index}")
            signals.append(
                self._signal(
                    evidence_id=f"conversation_{message_id}",
                    source="conversation",
                    kind="message",
                    value={
                        "role": message.get("role", "unknown"),
                        "text": text[:1000],
                    },
                    observed_at=observed,
                    now=now,
                    fresh_seconds=self.conversation_fresh_seconds,
                    stale_seconds=self.conversation_stale_seconds,
                    confidence=1.0,
                )
            )
        return signals

    @staticmethod
    def _message_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts = [
            str(part.get("text") or "").strip()
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)

    async def _event_signals(
        self,
        scope: MemoryScope,
        now: datetime,
    ) -> list[SituationSignal]:
        if self.event_memory_service is None:
            return []
        events = await self.event_memory_service.search(scope, limit=12)
        signals: list[SituationSignal] = []
        for event in events:
            status = str(event.get("status") or "")
            occurred_start = _parse_time(event.get("occurred_start"))
            occurred_end = _parse_time(event.get("occurred_end"))
            current_until = _parse_time(event.get("current_until"))
            active_until = current_until or occurred_end
            active_now = status in {
                EventStatus.planned.value,
                EventStatus.in_progress.value,
            } and occurred_start is not None and occurred_start <= now and (
                active_until is not None and active_until >= now
            )
            observed = occurred_start or _parse_time(event.get("updated_at"))
            event_key = str(event.get("event_key") or event.get("id") or "event")
            freshness = SignalFreshness.fresh if active_now else None
            signals.append(
                self._signal(
                    evidence_id=f"event_{event.get('id') or event_key}",
                    source="event_memory",
                    kind=str(event.get("kind") or "event"),
                    value={
                        "event_key": event_key,
                        "thread_key": event.get("thread_key"),
                        "status": status,
                        "active_now": active_now,
                        "occurred_start": event.get("occurred_start"),
                        "occurred_end": event.get("occurred_end"),
                        "current_until": event.get("current_until"),
                        "summary": str(event.get("core_summary") or "")[:1000],
                        "location": event.get("location"),
                    },
                    observed_at=observed,
                    now=now,
                    fresh_seconds=self.conversation_fresh_seconds,
                    stale_seconds=self.conversation_stale_seconds,
                    confidence=float(event.get("confidence") or 0.5),
                    freshness=freshness,
                )
            )
        return signals

    def _gps_signals(
        self,
        scope: MemoryScope,
        now: datetime,
    ) -> list[SituationSignal]:
        history = self.presence.location_history(scope.user_id, scope.agent_id, limit=2)
        if not history:
            return []
        latest = history[0]
        movement = "unknown"
        distance_m: float | None = None
        speed_mps: float | None = None
        if len(history) > 1:
            previous = history[1]
            elapsed = (
                _aware(latest.reported_at) - _aware(previous.reported_at)
            ).total_seconds()
            if 0 < elapsed <= 1800:
                distance_m = self._distance_m(previous, latest)
                speed_mps = distance_m / elapsed
                if distance_m <= 100:
                    movement = "stationary"
                elif distance_m >= 250 and speed_mps >= 0.3:
                    movement = "moving"
        return [
            self._signal(
                evidence_id="gps_latest",
                source="gps",
                kind="last_reported_location",
                value={
                    "latitude": latest.latitude,
                    "longitude": latest.longitude,
                    "movement": movement,
                    "distance_m": round(distance_m) if distance_m is not None else None,
                    "speed_mps": round(speed_mps, 2) if speed_mps is not None else None,
                    "location_source": "device_report",
                },
                observed_at=_aware(latest.reported_at),
                now=now,
                fresh_seconds=self.gps_fresh_seconds,
                stale_seconds=self.gps_stale_seconds,
                confidence=0.9,
            )
        ]

    @staticmethod
    def _distance_m(first: LocationReading, second: LocationReading) -> float:
        radius = 6_371_000.0
        lat1 = math.radians(first.latitude)
        lat2 = math.radians(second.latitude)
        delta_lat = lat2 - lat1
        delta_lon = math.radians(second.longitude - first.longitude)
        value = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        return 2 * radius * math.asin(min(1.0, math.sqrt(value)))

    async def _weather_signals(
        self,
        scope: MemoryScope,
        now: datetime,
    ) -> list[SituationSignal]:
        if self.weather_service is None:
            return []
        reading = self.presence.location_reading(scope.user_id, scope.agent_id)
        location_source = "configured_fallback"
        location_age: float | None = None
        if reading is not None:
            location_age = max(
                0.0,
                (now - _aware(reading.reported_at)).total_seconds(),
            )
        if reading is not None and location_age is not None:
            if location_age <= self.gps_stale_seconds:
                latitude = reading.latitude
                longitude = reading.longitude
                location_source = "device_report"
            else:
                reading = None
        if reading is None:
            latitude = self.weather_service.latitude
            longitude = self.weather_service.longitude
            if latitude is None or longitude is None:
                return []

        cache_key = f"{scope.agent_id}:{scope.user_id}"
        cache = self._weather_cache.get(cache_key)
        if cache is not None:
            cached_at, cached_lat, cached_lon, cached_data, cached_source = cache
            cache_age = (now - cached_at).total_seconds()
            if (
                cache_age <= min(self.weather_fresh_seconds, 900)
                and abs(cached_lat - float(latitude)) < 0.02
                and abs(cached_lon - float(longitude)) < 0.02
            ):
                return [
                    self._weather_signal(
                        cached_data,
                        cached_at,
                        now,
                        cached_source,
                        location_age,
                    )
                ]

        data = await asyncio.wait_for(
            self.weather_service.fetch_current(float(latitude), float(longitude)),
            timeout=self.weather_timeout_seconds,
        )
        if not data:
            return []
        self._weather_cache[cache_key] = (
            now,
            float(latitude),
            float(longitude),
            data,
            location_source,
        )
        return [
            self._weather_signal(
                data,
                now,
                now,
                location_source,
                location_age,
            )
        ]

    def _weather_signal(
        self,
        data: dict[str, Any],
        observed_at: datetime,
        now: datetime,
        location_source: str,
        location_age: float | None,
    ) -> SituationSignal:
        return self._signal(
            evidence_id="weather_current",
            source="weather",
            kind="current_conditions",
            value={
                **data,
                "location_source": location_source,
                "location_age_seconds": (
                    round(location_age) if location_age is not None else None
                ),
                "is_user_current_location": (
                    location_source == "device_report"
                    and location_age is not None
                    and location_age <= self.gps_fresh_seconds
                ),
            },
            observed_at=observed_at,
            now=now,
            fresh_seconds=self.weather_fresh_seconds,
            stale_seconds=self.weather_stale_seconds,
            confidence=(
                0.9
                if location_source == "device_report"
                and location_age is not None
                and location_age <= self.gps_fresh_seconds
                else 0.65
                if location_source == "device_report"
                else 0.5
            ),
        )

    def _health_signals(
        self,
        scope: MemoryScope,
        now: datetime,
        timezone_name: str,
    ) -> list[SituationSignal]:
        if self.health_store is None:
            return []
        signals: list[SituationSignal] = []
        for sample_type in (
            "SLEEP_STAGE",
            "HEART_RATE",
            "RESTING_HEART_RATE",
            "WORKOUT",
        ):
            sample = self.health_store.latest_sample(scope.user_id, sample_type)
            if sample is None:
                continue
            observed = _parse_time(sample.bucket_end) or _parse_time(sample.bucket_start)
            in_progress = False
            start = _parse_time(sample.bucket_start)
            end = _parse_time(sample.bucket_end)
            if sample_type == "WORKOUT" and start is not None and end is not None:
                in_progress = start <= now <= end
            fresh_seconds = 900 if sample_type == "SLEEP_STAGE" else self.health_fresh_seconds
            stale_seconds = 1800 if sample_type == "SLEEP_STAGE" else self.health_stale_seconds
            signals.append(
                self._signal(
                    evidence_id=f"health_{sample_type.lower()}_latest",
                    source="health",
                    kind=sample_type.lower(),
                    value={
                        "bucket_start": sample.bucket_start,
                        "bucket_end": sample.bucket_end,
                        "value1": sample.value1,
                        "value2": sample.value2,
                        "value3": sample.value3,
                        "value4": sample.value4,
                        "in_progress": in_progress,
                    },
                    observed_at=observed,
                    now=now,
                    fresh_seconds=fresh_seconds,
                    stale_seconds=stale_seconds,
                    confidence=float(sample.quality or 0.8),
                )
            )

        local_day = now.astimezone(resolve_zoneinfo(timezone_name)).date().isoformat()
        metrics, _ = self.health_store.get_metrics(
            scope.user_id,
            local_day,
            local_day,
            timezone_name,
        )
        selected_metrics = [
            metric
            for metric in metrics
            if metric.metric_type
            in {"STEPS", "HEART_RATE", "RESTING_HEART_RATE", "SLEEP"}
        ]
        if selected_metrics:
            observed = max(
                (_parse_time(metric.updated_at) for metric in selected_metrics),
                default=None,
                key=lambda item: item or datetime.min.replace(tzinfo=timezone.utc),
            )
            signals.append(
                self._signal(
                    evidence_id="health_today_metrics",
                    source="health",
                    kind="today_metrics",
                    value={
                        metric.metric_type: {
                            "day": metric.day,
                            "value1": metric.value1,
                            "value2": metric.value2,
                            "value3": metric.value3,
                        }
                        for metric in selected_metrics
                    },
                    observed_at=observed,
                    now=now,
                    fresh_seconds=self.health_fresh_seconds,
                    stale_seconds=self.health_stale_seconds,
                    confidence=0.8,
                )
            )
        return signals

    def _reminder_signals(
        self,
        scope: MemoryScope,
        now: datetime,
    ) -> list[SituationSignal]:
        reminders = self.reminder_store.list_pending(scope.user_id, scope.agent_id)[:8]
        return [
            self._signal(
                evidence_id=f"reminder_{item.id}",
                source="reminder",
                kind=item.kind.value,
                value={
                    "message": item.message,
                    "due_at": item.due_at.isoformat() if item.due_at else None,
                    "metric_type": item.metric_type,
                    "operator": item.operator,
                    "threshold": item.threshold,
                },
                observed_at=item.due_at or item.created_at,
                now=now,
                fresh_seconds=10**9,
                stale_seconds=10**9,
                freshness=SignalFreshness.fresh,
                confidence=1.0,
            )
            for item in reminders
        ]

    def _todo_signals(
        self,
        scope: MemoryScope,
        now: datetime,
    ) -> list[SituationSignal]:
        todos = self.todo_store.list(
            scope.user_id,
            scope.agent_id,
            status=TodoStatus.open,
        )[:8]
        return [
            self._signal(
                evidence_id=f"todo_{item.id}",
                source="todo",
                kind="open",
                value={"title": item.title},
                observed_at=item.created_at,
                now=now,
                fresh_seconds=10**9,
                stale_seconds=10**9,
                freshness=SignalFreshness.fresh,
                confidence=1.0,
            )
            for item in todos
        ]

    def _presence_signals(
        self,
        scope: MemoryScope,
        now: datetime,
    ) -> list[SituationSignal]:
        last_seen = self.presence.last_seen(scope.user_id, scope.agent_id)
        if last_seen is None:
            return []
        online = (now - _aware(last_seen)).total_seconds() <= self.presence_ttl_seconds
        return [
            self._signal(
                evidence_id="presence_latest",
                source="presence",
                kind="chat_presence",
                value={"online": online},
                observed_at=_aware(last_seen),
                now=now,
                fresh_seconds=self.presence_ttl_seconds,
                stale_seconds=max(300, self.presence_ttl_seconds * 12),
                confidence=1.0,
            )
        ]

    def _pacing_signals(
        self,
        scope: MemoryScope,
        now: datetime,
    ) -> list[SituationSignal]:
        state = self.pacing_store.get_state(scope.user_id, scope.agent_id)
        if state is None:
            return []
        sent = int(state.get("total_sent") or 0)
        replied = int(state.get("total_replied") or 0)
        return [
            SituationSignal(
                id="pacing_state",
                source="pacing",
                kind="reply_behavior",
                value={
                    **state,
                    "reply_rate": replied / sent if sent else None,
                },
                observed_at=now,
                age_seconds=0,
                freshness=SignalFreshness.fresh,
                confidence=1.0,
            )
        ]

    def _schedule_signals(self, scope: MemoryScope, now: datetime) -> list[SituationSignal]:
        if self.schedule_service is None:
            return []
        day = now.astimezone(resolve_zoneinfo(self._timezone(scope.user_id))).date()
        from datetime import timedelta
        events = self.schedule_service.list(scope.user_id, scope.agent_id, day, day + timedelta(days=2))["events"]
        result = []
        for event in events:
            if event["status"] != "scheduled" or datetime.fromisoformat(event["ends_at"]) <= now:
                continue
            result.append(SituationSignal(
                id=f"schedule_{event['id']}_{event['occurrence_date']}", source="schedule", kind="planned_event",
                value={**event, "planned_only": True, "active_now": not event["all_day"] and datetime.fromisoformat(event["starts_at"]) <= now < datetime.fromisoformat(event["ends_at"])},
                observed_at=now, age_seconds=0, freshness=SignalFreshness.fresh, confidence=1.0,
            ))
        return sorted(result, key=lambda item: (not item.value["active_now"], item.value["starts_at"]))[:8]

    def _observation_signals(
        self,
        scope: MemoryScope,
        now: datetime,
    ) -> list[SituationSignal]:
        signals: list[SituationSignal] = []
        for source in (
            ObservationSource.health,
            ObservationSource.weather,
            ObservationSource.phone_state,
        ):
            observations = self.observation_service.query(
                user_id=scope.user_id,
                agent_id=scope.agent_id,
                source=source,
                limit=2,
            )
            for observation in observations:
                fresh_seconds = (
                    self.weather_fresh_seconds
                    if source is ObservationSource.weather
                    else self.health_fresh_seconds
                )
                stale_seconds = (
                    self.weather_stale_seconds
                    if source is ObservationSource.weather
                    else self.health_stale_seconds
                )
                signals.append(
                    self._signal(
                        evidence_id=f"observation_{observation.id}",
                        source="observation",
                        kind=f"{source.value}/{observation.kind}",
                        value=dict(observation.payload),
                        observed_at=_aware(observation.observed_at),
                        now=now,
                        fresh_seconds=fresh_seconds,
                        stale_seconds=stale_seconds,
                        confidence=0.8,
                    )
                )
        return signals

    @staticmethod
    def _apply_interruptibility(snapshot: SituationSnapshot) -> None:
        hard_reasons: list[tuple[str, str]] = []
        low_reasons: list[tuple[str, str]] = []
        for signal in snapshot.signals:
            if signal.freshness is SignalFreshness.expired:
                continue
            if signal.source == "schedule" and signal.value.get("active_now"):
                low_reasons.append(("日历安排显示此时可能有事，避免无关闲聊；不代表实际正在进行", signal.id))
            if signal.source == "health" and signal.kind == "sleep_stage":
                if signal.freshness is SignalFreshness.fresh:
                    try:
                        stage = int(signal.value.get("value1"))
                    except (TypeError, ValueError):
                        stage = 0
                    if stage in (1, 2, 3):
                        hard_reasons.append(("新鲜睡眠分期显示用户正在睡眠", signal.id))
            if signal.source == "health" and signal.kind == "workout":
                if signal.freshness is SignalFreshness.fresh and signal.value.get(
                    "in_progress"
                ):
                    low_reasons.append(("健康数据表明运动可能仍在进行", signal.id))
            if signal.source == "gps" and signal.value.get("movement") == "moving":
                if signal.freshness is SignalFreshness.fresh:
                    low_reasons.append(("位置变化显示用户可能正在移动", signal.id))
            if signal.source == "event_memory" and signal.value.get("active_now"):
                summary = str(signal.value.get("summary") or "").lower()
                hard_terms = (
                    "请勿打扰",
                    "别打扰",
                    "开会",
                    "会议中",
                    "考试",
                    "开车",
                    "驾驶",
                    "睡觉",
                    "asleep",
                    "meeting",
                    "driving",
                )
                if any(term in summary for term in hard_terms):
                    hard_reasons.append(("活动事件表明当前不适合打扰", signal.id))

        user_messages = [
            signal
            for signal in snapshot.signals
            if signal.source == "conversation"
            and signal.value.get("role") == "user"
            and signal.freshness is SignalFreshness.fresh
        ]
        if user_messages:
            latest = max(
                user_messages,
                key=lambda item: item.observed_at
                or datetime.min.replace(tzinfo=timezone.utc),
            )
            text = str(latest.value.get("text") or "")
            completed_terms = ("忙完", "开完会", "考完", "醒了", "不用等了")
            busy_terms = ("别打扰", "不要打扰", "我在忙", "正在开会", "在考试", "在开车")
            if not any(term in text for term in completed_terms) and any(
                term in text for term in busy_terms
            ):
                hard_reasons.append(("用户最近明确表示当前不方便", latest.id))

        selected = hard_reasons or low_reasons
        if hard_reasons:
            snapshot.interruptibility = Interruptibility.do_not_interrupt
        elif low_reasons:
            snapshot.interruptibility = Interruptibility.low
        else:
            snapshot.interruptibility = Interruptibility.normal
        snapshot.interruptibility_reasons = [reason for reason, _ in selected]
        snapshot.interruptibility_evidence = [evidence for _, evidence in selected]
