from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field


class ToldFeedback(str, Enum):
    none = "none"
    helpful = "helpful"
    unhelpful = "unhelpful"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Told(BaseModel):
    """Record of what has already been surfaced to the user, keyed by insight."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    agent_id: str = "default"
    insight_key: str
    category: str | None = None
    told_at: datetime = Field(default_factory=_utcnow)
    user_feedback: ToldFeedback = ToldFeedback.none
    replied: bool = False


class ToldStore:
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
                CREATE TABLE IF NOT EXISTS told (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    insight_key TEXT NOT NULL,
                    category TEXT,
                    told_at TEXT NOT NULL,
                    user_feedback TEXT NOT NULL,
                    replied INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            self._ensure_column(connection, "told", "category", "TEXT")
            self._ensure_column(
                connection, "told", "replied", "INTEGER NOT NULL DEFAULT 0"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_told_scope ON told(user_id, agent_id, insight_key)"
            )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        ddl: str,
    ) -> None:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def save(self, told: Told) -> Told:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO told
                    (id, user_id, agent_id, insight_key, category, told_at, user_feedback, replied)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    told.id,
                    told.user_id,
                    told.agent_id,
                    told.insight_key,
                    told.category,
                    told.told_at.isoformat(),
                    told.user_feedback.value,
                    int(told.replied),
                ),
            )
        return told

    def has_told(self, insight_key: str, user_id: str, agent_id: str = "default") -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM told WHERE insight_key = ? AND user_id = ? AND agent_id = ?",
                (insight_key, user_id, agent_id),
            ).fetchone()
        return row is not None

    def get(
        self,
        insight_key: str,
        user_id: str,
        agent_id: str = "default",
    ) -> Told | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM told WHERE insight_key = ? AND user_id = ? AND agent_id = ?",
                (insight_key, user_id, agent_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def update_feedback(
        self,
        insight_key: str,
        user_id: str,
        agent_id: str,
        feedback: ToldFeedback,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE told SET user_feedback = ? "
                "WHERE insight_key = ? AND user_id = ? AND agent_id = ?",
                (feedback.value, insight_key, user_id, agent_id),
            )
        return cursor.rowcount > 0

    def mark_replied(
        self,
        insight_key: str,
        user_id: str,
        agent_id: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE told SET replied = 1 "
                "WHERE insight_key = ? AND user_id = ? AND agent_id = ?",
                (insight_key, user_id, agent_id),
            )
        return cursor.rowcount > 0

    def list(self, user_id: str, agent_id: str = "default") -> list[Told]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM told WHERE user_id = ? AND agent_id = ? ORDER BY told_at DESC",
                (user_id, agent_id),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM told WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )

    def delete(self, told_id: str, user_id: str, agent_id: str = "default") -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM told WHERE id = ? AND user_id = ? AND agent_id = ?",
                (told_id, user_id, agent_id),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Told:
        return Told(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            insight_key=row["insight_key"],
            category=row["category"],
            told_at=datetime.fromisoformat(row["told_at"]),
            user_feedback=ToldFeedback(row["user_feedback"]),
            replied=bool(row["replied"]),
        )
