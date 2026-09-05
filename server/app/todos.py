from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field


class TodoStatus(str, Enum):
    open = "open"
    done = "done"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Todo(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    agent_id: str = "default"
    title: str
    status: TodoStatus = TodoStatus.open
    created_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None


class TodoStore:
    """SQLite-backed per-user todo list."""

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
                CREATE TABLE IF NOT EXISTS todos (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_todos_scope "
                "ON todos(user_id, agent_id, status)"
            )

    def add(self, todo: Todo) -> Todo:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO todos
                    (id, user_id, agent_id, title, status, created_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    todo.id,
                    todo.user_id,
                    todo.agent_id,
                    todo.title,
                    todo.status.value,
                    todo.created_at.isoformat(),
                    todo.completed_at.isoformat() if todo.completed_at else None,
                ),
            )
        return todo

    def list(
        self,
        user_id: str,
        agent_id: str = "default",
        status: TodoStatus | None = None,
    ) -> list[Todo]:
        sql = "SELECT * FROM todos WHERE user_id = ? AND agent_id = ?"
        params: list[object] = [user_id, agent_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status.value)
        sql += " ORDER BY created_at DESC"
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._from_row(row) for row in rows]

    def complete(self, todo_id: str, user_id: str, agent_id: str = "default") -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE todos
                SET status = ?, completed_at = ?
                WHERE id = ? AND user_id = ? AND agent_id = ? AND status = ?
                """,
                (
                    TodoStatus.done.value,
                    _utcnow().isoformat(),
                    todo_id,
                    user_id,
                    agent_id,
                    TodoStatus.open.value,
                ),
            )
        return cursor.rowcount > 0

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM todos WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Todo:
        return Todo(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            title=row["title"],
            status=TodoStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"])
            if row["completed_at"]
            else None,
        )
