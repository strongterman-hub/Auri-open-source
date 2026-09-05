from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from app.memory.event_extraction import EventExtractor
from app.memory.events import EventCandidate, EventMemoryStore, EventRecord, EventStatus
from app.memory.models import MemoryScope, OriginClass
from app.observation.models import Observation, ObservationSource
from app.observation.store import ObservationStore
from app.services.timezone_store import resolve_zoneinfo


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class EventMemoryService:
    """Captures owner messages, updates the event timeline, and builds recall context."""

    def __init__(
        self,
        store: EventMemoryStore,
        observation_store: ObservationStore,
        *,
        extractor: EventExtractor | None = None,
        timezone_resolver: Callable[[str], str] | None = None,
        context_limit: int = 12,
        detail_threshold: float = 0.28,
        low_half_life_days: float = 14.0,
        normal_half_life_days: float = 45.0,
        high_half_life_days: float = 180.0,
        current_state_ttl_hours: float = 8.0,
    ) -> None:
        self.store = store
        self.observation_store = observation_store
        self.extractor = extractor
        self.timezone_resolver = timezone_resolver
        self.context_limit = context_limit
        self.detail_threshold = detail_threshold
        self.low_half_life_days = low_half_life_days
        self.normal_half_life_days = normal_half_life_days
        self.high_half_life_days = high_half_life_days
        self.current_state_ttl_hours = current_state_ttl_hours

    def _timezone(self, user_id: str) -> str:
        if self.timezone_resolver is not None:
            value = self.timezone_resolver(user_id)
            if value:
                return value
        return "Asia/Shanghai"

    @staticmethod
    def _message_text(content: str | list[dict[str, Any]]) -> str:
        if isinstance(content, str):
            return content.strip()
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                text = str(part.get("text") or "").strip()
                if text:
                    parts.append(text)
            elif part.get("type") == "image_url":
                parts.append("[图片]")
            elif part.get("type") == "file":
                file_info = part.get("file") or {}
                parts.append(f"[文件：{file_info.get('name', 'file')}]")
        return "\n".join(parts).strip()

    @staticmethod
    def _message_at(message: dict[str, Any]) -> datetime:
        value = message.get("timestamp")
        if isinstance(value, str):
            try:
                return _aware(datetime.fromisoformat(value))
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    @staticmethod
    def _conversation_observation_id(
        user_id: str,
        agent_id: str,
        session_id: str,
        message_id: str,
    ) -> str:
        raw = f"conversation:{user_id}:{agent_id}:{session_id}:{message_id}"
        return "conv_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()

    async def capture_user_message(
        self,
        scope: MemoryScope,
        session_id: str,
        message: dict[str, Any],
    ) -> list[EventRecord]:
        """Persist the raw owner source first, then best-effort extract structured events."""
        text = self._message_text(message.get("content", ""))
        message_id = str(message.get("id") or "")
        message_at = self._message_at(message)
        source_ref = f"session:{session_id}:message:{message_id}"
        observation = Observation(
            id=self._conversation_observation_id(
                scope.user_id,
                scope.agent_id,
                session_id,
                message_id,
            ),
            user_id=scope.user_id,
            agent_id=scope.agent_id,
            source=ObservationSource.conversation,
            observed_at=message_at,
            origin=OriginClass.owner,
            kind="user_message",
            payload={
                "session_id": session_id,
                "message_id": message_id,
                "text": text,
            },
            supersession_key=source_ref,
        )
        await asyncio.to_thread(self.observation_store.add, observation)

        if self.extractor is None or not text:
            return []
        recent = await asyncio.to_thread(
            self.store.query,
            scope.user_id,
            scope.agent_id,
            limit=20,
        )
        try:
            candidates = await self.extractor.extract(
                user_id=scope.user_id,
                message_text=text,
                message_at=message_at,
                timezone_name=self._timezone(scope.user_id),
                recent_events=recent,
            )
        except Exception:
            return []

        records: list[EventRecord] = []
        for candidate in candidates:
            normalized = self._normalize_candidate(candidate, message_at, scope.user_id)
            try:
                record = await asyncio.to_thread(
                    self.store.upsert_candidate,
                    user_id=scope.user_id,
                    agent_id=scope.agent_id,
                    candidate=normalized,
                    origin=OriginClass.owner,
                    source_ref=source_ref,
                    now=message_at,
                )
                await asyncio.to_thread(
                    self.store.refresh_temporal_relations,
                    scope.user_id,
                    scope.agent_id,
                    record.thread_key,
                    now=message_at,
                )
                records.append(record)
            except Exception:
                continue
        return records

    def _normalize_candidate(
        self,
        candidate: EventCandidate,
        message_at: datetime,
        user_id: str,
    ) -> EventCandidate:
        data = candidate.model_dump()
        data["timezone"] = candidate.timezone or self._timezone(user_id)
        if candidate.status is EventStatus.in_progress and candidate.current_until is None:
            data["current_until"] = message_at + timedelta(hours=self.current_state_ttl_hours)
        return EventCandidate.model_validate(data)

    async def build_context(
        self,
        scope: MemoryScope,
        *,
        now: datetime | None = None,
        query: str | None = None,
        limit: int | None = None,
    ) -> str:
        current = _aware(now or datetime.now(timezone.utc))
        await asyncio.to_thread(
            self.store.apply_decay,
            scope.user_id,
            scope.agent_id,
            now=current,
            threshold=self.detail_threshold,
            low_half_life_days=self.low_half_life_days,
            normal_half_life_days=self.normal_half_life_days,
            high_half_life_days=self.high_half_life_days,
        )
        active = await asyncio.to_thread(
            self.store.active,
            scope.user_id,
            scope.agent_id,
            now=current,
            limit=10,
        )
        recent = await asyncio.to_thread(
            self.store.query,
            scope.user_id,
            scope.agent_id,
            query=query,
            limit=limit or self.context_limit,
        )
        selected: list[EventRecord] = []
        seen: set[str] = set()
        for event in [*active, *recent]:
            if event.id in seen:
                continue
            selected.append(event)
            seen.add(event.id)
        if not selected:
            return "No structured event memory is available yet."

        selected.sort(
            key=lambda event: _aware(event.occurred_start or event.updated_at)
        )
        event_keys = {event.id: event.event_key for event in selected}
        lines = [
            "STRUCTURED EVENT TIMELINE (authoritative chronology):",
            "- Keep unknown time/location unknown. Never infer that two facts "
            "happened at the same place unless their time intervals and sources support it.",
            "- planned is not completed; an expired current state is not the user's present state.",
        ]
        for event in selected:
            start = event.occurred_start.isoformat() if event.occurred_start else "unknown"
            end = event.occurred_end.isoformat() if event.occurred_end else "unknown"
            current_until = (
                event.current_until.isoformat() if event.current_until else "unbounded"
            )
            active_now = event.status in {EventStatus.planned, EventStatus.in_progress} and (
                event.current_until is None or _aware(event.current_until) >= current
            )
            line = (
                f"- [{start} .. {end}] key={event.event_key} status={event.status.value} "
                f"active_now={str(active_now).lower()} current_until={current_until} "
                f"thread={event.thread_key or 'none'} kind={event.kind} "
                f"origin={event.origin.value}: {event.core_summary}"
            )
            if event.location:
                line += f"; location={event.location}"
            visible_details: list[str] = []
            for detail in event.details:
                strength = self.store.detail_strength(
                    detail,
                    now=current,
                    low_half_life_days=self.low_half_life_days,
                    normal_half_life_days=self.normal_half_life_days,
                    high_half_life_days=self.high_half_life_days,
                )
                if detail.forgotten_at is None and strength >= self.detail_threshold:
                    visible_details.append(detail.content)
            if visible_details:
                line += "; remembered_details=" + " | ".join(visible_details[:4])
            relation_text = [
                f"{relation.relation.value}:"
                f"{event_keys.get(relation.target_event_id, relation.target_event_id)}"
                for relation in event.relations
            ]
            if relation_text:
                line += "; relations=" + " | ".join(relation_text)
            if event.source_refs:
                line += "; sources=" + " | ".join(event.source_refs[-3:])
            lines.append(line)
        return "\n".join(lines)

    async def search(
        self,
        scope: MemoryScope,
        *,
        query: str | None = None,
        limit: int = 50,
        include_forgotten_details: bool = False,
    ) -> list[dict[str, Any]]:
        events = await asyncio.to_thread(
            self.store.query,
            scope.user_id,
            scope.agent_id,
            query=query,
            limit=limit,
        )
        payload: list[dict[str, Any]] = []
        for event in events:
            item = event.model_dump(mode="json")
            if not include_forgotten_details:
                item["details"] = [
                    detail.model_dump(mode="json")
                    for detail in event.details
                    if detail.forgotten_at is None
                ]
            payload.append(item)
        return payload

    def now_for_user(self, user_id: str) -> datetime:
        return datetime.now(resolve_zoneinfo(self._timezone(user_id)))
