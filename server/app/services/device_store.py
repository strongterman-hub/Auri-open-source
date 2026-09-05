from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StoredLocation:
    latitude: float
    longitude: float
    reported_at: datetime


class DeviceStore:
    """Durable registration of push device tokens across server restarts."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_tokens (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    token TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, agent_id, token)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_device_tokens_scope "
                "ON device_tokens(user_id, agent_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS device_locations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    latitude REAL NOT NULL,
                    longitude REAL NOT NULL,
                    reported_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_device_locations_scope_time "
                "ON device_locations(user_id, agent_id, reported_at DESC)"
            )

    def register(self, user_id: str, agent_id: str, token: str) -> bool:
        if not token.strip():
            return False
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO device_tokens
                    (user_id, agent_id, token, registered_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, agent_id, token, _utcnow().isoformat()),
            )
        return cursor.rowcount > 0

    def unregister(self, user_id: str, agent_id: str, token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                DELETE FROM device_tokens
                WHERE user_id = ? AND agent_id = ? AND token = ?
                """,
                (user_id, agent_id, token),
            )
        return cursor.rowcount > 0

    def tokens(self, user_id: str, agent_id: str = "default") -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT token FROM device_tokens
                WHERE user_id = ? AND agent_id = ?
                ORDER BY registered_at
                """,
                (user_id, agent_id),
            ).fetchall()
        return [row["token"] for row in rows]

    def all_tokens(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT token FROM device_tokens
                ORDER BY registered_at
                """
            ).fetchall()
        return [row["token"] for row in rows]

    def update_location(
        self,
        user_id: str,
        agent_id: str,
        latitude: float,
        longitude: float,
        reported_at: datetime | None = None,
    ) -> None:
        """Persist only the two newest positions needed for movement context."""
        observed = reported_at or _utcnow()
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO device_locations
                    (user_id, agent_id, latitude, longitude, reported_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    agent_id,
                    float(latitude),
                    float(longitude),
                    observed.isoformat(),
                ),
            )
            connection.execute(
                """
                DELETE FROM device_locations
                WHERE user_id = ? AND agent_id = ? AND id NOT IN (
                    SELECT id FROM device_locations
                    WHERE user_id = ? AND agent_id = ?
                    ORDER BY reported_at DESC, id DESC
                    LIMIT 2
                )
                """,
                (user_id, agent_id, user_id, agent_id),
            )

    def location_history(
        self,
        user_id: str,
        agent_id: str = "default",
        limit: int = 2,
    ) -> list[StoredLocation]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT latitude, longitude, reported_at
                FROM device_locations
                WHERE user_id = ? AND agent_id = ?
                ORDER BY reported_at DESC, id DESC
                LIMIT ?
                """,
                (user_id, agent_id, max(1, min(int(limit), 10))),
            ).fetchall()
        return [
            StoredLocation(
                latitude=float(row["latitude"]),
                longitude=float(row["longitude"]),
                reported_at=datetime.fromisoformat(row["reported_at"]),
            )
            for row in rows
        ]

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM device_tokens WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )
            connection.execute(
                "DELETE FROM device_locations WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )
