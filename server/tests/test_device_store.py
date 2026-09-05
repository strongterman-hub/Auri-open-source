from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.device_store import DeviceStore
from app.services.presence_service import PresenceService


def test_device_store_persists_tokens_across_instances(tmp_dir: Path) -> None:
    path = tmp_dir / "devices" / "devices.db"

    first = DeviceStore(path)
    assert first.register("u1", "default", "tok-1") is True
    assert first.register("u1", "default", "tok-1") is False
    assert first.register("u1", "default", "tok-2") is True
    assert first.tokens("u1") == ["tok-1", "tok-2"]

    second = DeviceStore(path)  # simulates a server restart
    assert second.tokens("u1") == ["tok-1", "tok-2"]
    assert second.unregister("u1", "default", "tok-1") is True
    assert second.tokens("u1") == ["tok-2"]


def test_presence_service_delegates_to_device_store(tmp_dir: Path) -> None:
    presence = PresenceService(device_store=DeviceStore(tmp_dir / "devices.db"))

    assert presence.register_device("u1", "default", "tok-1") is True
    assert presence.register_device("u1", "default", "tok-1") is False
    assert presence.device_tokens("u1") == ["tok-1"]
    assert presence.unregister_device("u1", "default", "tok-1") is True
    assert presence.device_tokens("u1") == []


def test_presence_service_without_store_keeps_memory_fallback(tmp_dir: Path) -> None:
    presence = PresenceService()

    assert presence.register_device("u1", "default", "tok-1") is True
    assert presence.register_device("u1", "default", "tok-1") is False
    assert presence.device_tokens("u1") == ["tok-1"]
    assert presence.unregister_device("u1", "default", "tok-1") is True
    assert presence.device_tokens("u1") == []


def test_device_store_persists_only_latest_two_locations(tmp_dir: Path) -> None:
    path = tmp_dir / "devices.db"
    store = DeviceStore(path)
    start = datetime(2026, 9, 3, 4, 0, tzinfo=timezone.utc)

    store.update_location("u1", "default", 39.0, 116.0, start)
    store.update_location(
        "u1", "default", 39.1, 116.1, start + timedelta(minutes=1)
    )
    store.update_location(
        "u1", "default", 39.2, 116.2, start + timedelta(minutes=2)
    )

    history = DeviceStore(path).location_history("u1")
    assert [(item.latitude, item.longitude) for item in history] == [
        (39.2, 116.2),
        (39.1, 116.1),
    ]


def test_presence_restores_location_after_restart_and_deletes_it(
    tmp_dir: Path,
) -> None:
    path = tmp_dir / "devices.db"
    first = PresenceService(device_store=DeviceStore(path))
    first.update_location("u1", "default", 39.9042, 116.4074)

    restarted = PresenceService(device_store=DeviceStore(path))
    reading = restarted.location_reading("u1")
    assert reading is not None
    assert reading.latitude == 39.9042
    assert reading.longitude == 116.4074

    restarted.delete_user("u1")
    assert DeviceStore(path).location_history("u1") == []
