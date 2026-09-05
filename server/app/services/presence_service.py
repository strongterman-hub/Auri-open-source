from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from app.services.device_store import DeviceStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class LocationReading:
    latitude: float
    longitude: float
    reported_at: datetime


class PresenceService:
    """Tracks whether a user currently has the chat screen open."""

    def __init__(self, device_store: DeviceStore | None = None) -> None:
        self._last_seen: dict[str, datetime] = {}
        self.device_store = device_store
        self._devices: dict[str, set[str]] = {}
        self._locations: dict[str, LocationReading] = {}
        self._location_waiters: dict[str, asyncio.Event] = {}

    @staticmethod
    def _key(user_id: str, agent_id: str) -> str:
        return f"{agent_id}:{user_id}"

    def heartbeat(self, user_id: str, agent_id: str = "default") -> datetime:
        now = _utcnow()
        self._last_seen[self._key(user_id, agent_id)] = now
        return now

    def last_seen(self, user_id: str, agent_id: str = "default") -> datetime | None:
        return self._last_seen.get(self._key(user_id, agent_id))

    def is_online(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        ttl_seconds: int = 180,
    ) -> bool:
        last_seen = self.last_seen(user_id, agent_id)
        if last_seen is None:
            return False
        return (_utcnow() - last_seen).total_seconds() <= ttl_seconds

    def register_device(self, user_id: str, agent_id: str, token: str) -> bool:
        if self.device_store is not None:
            return self.device_store.register(user_id, agent_id, token)
        key = self._key(user_id, agent_id)
        tokens = self._devices.setdefault(key, set())
        if token in tokens:
            return False
        tokens.add(token)
        return True

    def unregister_device(self, user_id: str, agent_id: str, token: str) -> bool:
        if self.device_store is not None:
            return self.device_store.unregister(user_id, agent_id, token)
        key = self._key(user_id, agent_id)
        tokens = self._devices.get(key)
        if tokens is None or token not in tokens:
            return False
        tokens.discard(token)
        return True

    def device_tokens(self, user_id: str, agent_id: str = "default") -> list[str]:
        if self.device_store is not None:
            return self.device_store.tokens(user_id, agent_id)
        return list(self._devices.get(self._key(user_id, agent_id), set()))

    def all_device_tokens(self) -> list[str]:
        if self.device_store is not None:
            return self.device_store.all_tokens()
        result: set[str] = set()
        for tokens in self._devices.values():
            result.update(tokens)
        return list(result)

    def update_location(
        self,
        user_id: str,
        agent_id: str,
        latitude: float,
        longitude: float,
    ) -> None:
        reading = LocationReading(
            latitude=float(latitude),
            longitude=float(longitude),
            reported_at=_utcnow(),
        )
        self._locations[self._key(user_id, agent_id)] = reading
        if self.device_store is not None:
            self.device_store.update_location(
                user_id,
                agent_id,
                reading.latitude,
                reading.longitude,
                reading.reported_at,
            )

    def location(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> tuple[float, float] | None:
        reading = self.location_reading(user_id, agent_id)
        if reading is None:
            return None
        return reading.latitude, reading.longitude

    def location_reading(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> LocationReading | None:
        reading = self._locations.get(self._key(user_id, agent_id))
        if reading is not None:
            return reading
        if self.device_store is None:
            return None
        persisted = self.device_store.location_history(user_id, agent_id, limit=1)
        if not persisted:
            return None
        stored = persisted[0]
        reading = LocationReading(
            latitude=stored.latitude,
            longitude=stored.longitude,
            reported_at=stored.reported_at,
        )
        self._locations[self._key(user_id, agent_id)] = reading
        return reading

    def location_history(
        self,
        user_id: str,
        agent_id: str = "default",
        limit: int = 2,
    ) -> list[LocationReading]:
        if self.device_store is not None:
            return [
                LocationReading(
                    latitude=item.latitude,
                    longitude=item.longitude,
                    reported_at=item.reported_at,
                )
                for item in self.device_store.location_history(
                    user_id,
                    agent_id,
                    limit=limit,
                )
            ]
        reading = self._locations.get(self._key(user_id, agent_id))
        return [reading] if reading is not None else []

    def begin_location_request(self, request_id: str, event: asyncio.Event) -> None:
        self._location_waiters[request_id] = event

    def resolve_location_request(self, request_id: str) -> None:
        event = self._location_waiters.pop(request_id, None)
        if event is not None:
            event.set()

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        key = self._key(user_id, agent_id)
        self._last_seen.pop(key, None)
        self._devices.pop(key, None)
        self._locations.pop(key, None)
        if self.device_store is not None:
            self.device_store.delete_user(user_id, agent_id)


class LocationRequester:
    """Coordinate an on-demand fresh location request with the streaming client."""

    def __init__(
        self,
        presence: PresenceService,
        emit: Callable[[str], Awaitable[None]],
        timeout: float = 8.0,
    ) -> None:
        self.presence = presence
        self.emit = emit
        self.timeout = timeout

    async def request(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> LocationReading | None:
        request_id = uuid4().hex
        event = asyncio.Event()
        self.presence.begin_location_request(request_id, event)
        try:
            await self.emit(request_id)
            try:
                await asyncio.wait_for(event.wait(), self.timeout)
            except asyncio.TimeoutError:
                return None
            return self.presence.location_reading(user_id, agent_id)
        finally:
            self.presence.resolve_location_request(request_id)
