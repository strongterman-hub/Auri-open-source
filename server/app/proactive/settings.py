from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProactiveSettingsStore:
    """Persistent per-user proactive-messaging switch."""

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
                CREATE TABLE IF NOT EXISTS proactive_settings (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    enabled INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, agent_id)
                )
                """
            )

    def is_enabled(self, user_id: str, agent_id: str = "default") -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled FROM proactive_settings WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            ).fetchone()
        return bool(row["enabled"]) if row else False

    def set_enabled(self, user_id: str, agent_id: str, enabled: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO proactive_settings
                    (user_id, agent_id, enabled, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, agent_id, int(enabled), _utcnow().isoformat()),
            )

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM proactive_settings WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )
