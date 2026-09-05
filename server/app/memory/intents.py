from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field


class IntentKind(str, Enum):
    time = "time"
    event = "event"


class IntentStatus(str, Enum):
    pending = "pending"
    armed = "armed"
    fired = "fired"
    done = "done"
    cancelled = "cancelled"
    expired = "expired"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Intent(BaseModel):
    """Prospective-memory intent. CRUD only; scheduling/triggering is a later window."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    agent_id: str = "default"
    kind: IntentKind
    trigger: dict = Field(default_factory=dict)
    cooldown_seconds: int = 86400
    fire_budget: int = 3
    expiry: datetime | None = None
    status: IntentStatus = IntentStatus.pending
    created_at: datetime = Field(default_factory=_utcnow)


class IntentStore:
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
                CREATE TABLE IF NOT EXISTS intents (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    kind TEXT NOT NULL,
                    trigger TEXT NOT NULL,
                    cooldown_seconds INTEGER NOT NULL,
                    fire_budget INTEGER NOT NULL,
                    expiry TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_intents_scope ON intents(user_id, agent_id, status)"
            )

    def save(self, intent: Intent) -> Intent:
        import json

        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO intents
                    (id, user_id, agent_id, kind, trigger, cooldown_seconds,
                     fire_budget, expiry, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent.id,
                    intent.user_id,
                    intent.agent_id,
                    intent.kind.value,
                    json.dumps(intent.trigger, ensure_ascii=False),
                    intent.cooldown_seconds,
                    intent.fire_budget,
                    intent.expiry.isoformat() if intent.expiry else None,
                    intent.status.value,
                    intent.created_at.isoformat(),
                ),
            )
        return intent

    def get(self, intent_id: str, user_id: str, agent_id: str = "default") -> Intent | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM intents WHERE id = ? AND user_id = ? AND agent_id = ?",
                (intent_id, user_id, agent_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def list(
        self,
        user_id: str,
        agent_id: str = "default",
        status: IntentStatus | None = None,
    ) -> list[Intent]:
        sql = "SELECT * FROM intents WHERE user_id = ? AND agent_id = ?"
        params: list[object] = [user_id, agent_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        sql += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._from_row(row) for row in rows]

    def delete(self, intent_id: str, user_id: str, agent_id: str = "default") -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM intents WHERE id = ? AND user_id = ? AND agent_id = ?",
                (intent_id, user_id, agent_id),
            )
        return cursor.rowcount > 0

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM intents WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Intent:
        import json

        return Intent(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            kind=IntentKind(row["kind"]),
            trigger=json.loads(row["trigger"]),
            cooldown_seconds=row["cooldown_seconds"],
            fire_budget=row["fire_budget"],
            expiry=datetime.fromisoformat(row["expiry"]) if row["expiry"] else None,
            status=IntentStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )
