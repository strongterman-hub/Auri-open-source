from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def normalize_timezone(name: str | None, default: str = "Asia/Shanghai") -> str:
    """Return a valid IANA timezone key, falling back to ``default`` then UTC."""
    for candidate in (name, default):
        if not candidate:
            continue
        try:
            return str(ZoneInfo(candidate).key)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return "UTC"


def resolve_zoneinfo(name: str | None, default: str = "Asia/Shanghai") -> ZoneInfo:
    """Resolve a timezone name to a ZoneInfo, never raising."""
    return ZoneInfo(normalize_timezone(name, default))


class UserTimezoneStore:
    """Persist one IANA timezone per user in a small JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._cache: dict[str, str] | None = None

    def _read(self) -> dict[str, str]:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = {}
            return self._cache
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            payload = {}
        self._cache = payload if isinstance(payload, dict) else {}
        return self._cache

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self.path)
        self._cache = data

    def set(self, user_id: str, timezone: str | None, default: str = "Asia/Shanghai") -> str:
        name = normalize_timezone(timezone, default)
        data = dict(self._read())
        data[user_id] = name
        self._write(data)
        return name

    def get(self, user_id: str) -> str | None:
        return self._read().get(user_id)

    def delete_user(self, user_id: str) -> None:
        data = dict(self._read())
        data.pop(user_id, None)
        self._write(data)


class TimezoneResolver:
    """Resolve a user's effective timezone, falling back to the server default."""

    def __init__(self, store: UserTimezoneStore, default_timezone: str = "Asia/Shanghai") -> None:
        self.store = store
        self.default_timezone = normalize_timezone(default_timezone)

    def get(self, user_id: str) -> str:
        return self.store.get(user_id) or self.default_timezone

    def zoneinfo(self, user_id: str) -> ZoneInfo:
        return resolve_zoneinfo(self.get(user_id), self.default_timezone)

    def set(self, user_id: str, timezone: str | None) -> str:
        return self.store.set(user_id, timezone, self.default_timezone)
