from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PreferenceStore:
    """Per-user, per-category reply-rate statistics for explore/exploit."""

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
                CREATE TABLE IF NOT EXISTS category_prefs (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    category TEXT NOT NULL,
                    sent_count INTEGER NOT NULL DEFAULT 0,
                    replied_count INTEGER NOT NULL DEFAULT 0,
                    last_chosen_at TEXT,
                    PRIMARY KEY (user_id, agent_id, category)
                )
                """
            )

    def record_sent(self, user_id: str, agent_id: str, category: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO category_prefs
                    (user_id, agent_id, category, sent_count, replied_count, last_chosen_at)
                VALUES (?, ?, ?, 1, 0, ?)
                ON CONFLICT(user_id, agent_id, category) DO UPDATE SET
                    sent_count = category_prefs.sent_count + 1,
                    last_chosen_at = excluded.last_chosen_at
                """,
                (user_id, agent_id, category, _utcnow().isoformat()),
            )

    def record_reply(self, user_id: str, agent_id: str, category: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO category_prefs
                    (user_id, agent_id, category, sent_count, replied_count, last_chosen_at)
                VALUES (?, ?, ?, 0, 1, ?)
                ON CONFLICT(user_id, agent_id, category) DO UPDATE SET
                    replied_count = category_prefs.replied_count + 1
                """,
                (user_id, agent_id, category, _utcnow().isoformat()),
            )

    def score(self, user_id: str, agent_id: str, category: str) -> float:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT sent_count, replied_count
                FROM category_prefs
                WHERE user_id = ? AND agent_id = ? AND category = ?
                """,
                (user_id, agent_id, category),
            ).fetchone()
        if row is None:
            return 0.5
        sent = int(row["sent_count"])
        replied = int(row["replied_count"])
        # Laplace-smoothed reply rate. Unexplored categories start at 0.5.
        return (replied + 1.0) / (sent + 2.0)

    def stats(self, user_id: str, agent_id: str = "default") -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT category, sent_count, replied_count
                FROM category_prefs
                WHERE user_id = ? AND agent_id = ?
                ORDER BY category
                """,
                (user_id, agent_id),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            sent = int(row["sent_count"])
            replied = int(row["replied_count"])
            result.append(
                {
                    "category": row["category"],
                    "sent": sent,
                    "replied": replied,
                    "score": (replied + 1.0) / (sent + 2.0),
                }
            )
        return result

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM category_prefs WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )
