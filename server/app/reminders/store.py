from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from app.reminders.models import Reminder, ReminderKind, ReminderStatus


class ReminderStore:
    """SQLite-backed reminder store."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    due_at TEXT,
                    metric_type TEXT,
                    operator TEXT,
                    threshold REAL,
                    status TEXT NOT NULL,
                    last_fired_at TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_reminders_scope "
                "ON reminders(user_id, agent_id, status)"
            )

    def save(self, reminder: Reminder) -> Reminder:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO reminders
                    (id, user_id, agent_id, kind, message, due_at, metric_type,
                     operator, threshold, status, last_fired_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reminder.id,
                    reminder.user_id,
                    reminder.agent_id,
                    reminder.kind.value,
                    reminder.message,
                    reminder.due_at.isoformat() if reminder.due_at else None,
                    reminder.metric_type,
                    reminder.operator,
                    reminder.threshold,
                    reminder.status.value,
                    reminder.last_fired_at.isoformat() if reminder.last_fired_at else None,
                    reminder.created_at.isoformat(),
                ),
            )
        return reminder

    def get(self, reminder_id: str, user_id: str, agent_id: str = "default") -> Reminder | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM reminders WHERE id = ? AND user_id = ? AND agent_id = ?",
                (reminder_id, user_id, agent_id),
            ).fetchone()
        return self._from_row(row) if row else None

    def list(
        self,
        user_id: str,
        agent_id: str = "default",
        status: ReminderStatus | None = None,
        kind: ReminderKind | None = None,
    ) -> list[Reminder]:
        sql = "SELECT * FROM reminders WHERE user_id = ? AND agent_id = ?"
        params: list[object] = [user_id, agent_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind.value)
        sql += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._from_row(row) for row in rows]

    def list_pending(self, user_id: str, agent_id: str = "default") -> list[Reminder]:
        return self.list(user_id, agent_id, status=ReminderStatus.pending)

    def delete(self, reminder_id: str, user_id: str, agent_id: str = "default") -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM reminders WHERE id = ? AND user_id = ? AND agent_id = ?",
                (reminder_id, user_id, agent_id),
            )
        return cursor.rowcount > 0

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM reminders WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )

    def mark_fired(
        self,
        reminder_id: str,
        user_id: str,
        agent_id: str,
        fired_at: datetime,
        *,
        done: bool,
    ) -> bool:
        status = ReminderStatus.done.value if done else ReminderStatus.pending.value
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE reminders
                SET last_fired_at = ?, status = ?
                WHERE id = ? AND user_id = ? AND agent_id = ?
                """,
                (fired_at.isoformat(), status, reminder_id, user_id, agent_id),
            )
        return cursor.rowcount > 0

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Reminder:
        return Reminder(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            kind=ReminderKind(row["kind"]),
            message=row["message"],
            due_at=datetime.fromisoformat(row["due_at"]) if row["due_at"] else None,
            metric_type=row["metric_type"],
            operator=row["operator"],
            threshold=row["threshold"],
            status=ReminderStatus(row["status"]),
            last_fired_at=datetime.fromisoformat(row["last_fired_at"])
            if row["last_fired_at"]
            else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )
