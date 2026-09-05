from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


OPEN_STATUSES = ("queued", "planning", "typing", "retry_wait")


@dataclass(frozen=True)
class ReplyJob:
    id: str
    session_id: str
    user_id: str
    agent_id: str
    status: str
    message_ids: list[str]
    planned: bool
    lane: str | None
    first_received_at: datetime
    last_received_at: datetime
    not_before: datetime
    attempt_count: int
    lease_until: datetime | None = None
    last_error: str | None = None


class ChatReplyStore:
    """Persistent queue and client-command store for asynchronous chat replies."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_reply_jobs (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    status TEXT NOT NULL,
                    message_ids TEXT NOT NULL,
                    planned INTEGER NOT NULL DEFAULT 0,
                    lane TEXT,
                    first_received_at TEXT NOT NULL,
                    last_received_at TEXT NOT NULL,
                    not_before TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    lease_until TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_chat_reply_due
                    ON chat_reply_jobs(status, not_before);
                CREATE INDEX IF NOT EXISTS idx_chat_reply_session
                    ON chat_reply_jobs(session_id, status, created_at);

                CREATE TABLE IF NOT EXISTS chat_client_commands (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    command_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    delivered_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_chat_commands_session
                    ON chat_client_commands(session_id, delivered_at, expires_at);
                """
            )

    @staticmethod
    def _job(row: sqlite3.Row) -> ReplyJob:
        return ReplyJob(
            id=row["id"],
            session_id=row["session_id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            status=row["status"],
            message_ids=list(json.loads(row["message_ids"] or "[]")),
            planned=bool(row["planned"]),
            lane=row["lane"],
            first_received_at=_parse(row["first_received_at"]) or _utcnow(),
            last_received_at=_parse(row["last_received_at"]) or _utcnow(),
            not_before=_parse(row["not_before"]) or _utcnow(),
            attempt_count=int(row["attempt_count"] or 0),
            lease_until=_parse(row["lease_until"]),
            last_error=row["last_error"],
        )

    def enqueue(
        self,
        *,
        session_id: str,
        user_id: str,
        agent_id: str,
        message_id: str,
        debounce_seconds: float,
    ) -> ReplyJob:
        now = _utcnow()
        debounce_at = now + timedelta(seconds=max(0.0, debounce_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM chat_reply_jobs
                WHERE session_id = ? AND status IN ('queued', 'retry_wait')
                ORDER BY created_at DESC LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if row is not None:
                ids = list(json.loads(row["message_ids"] or "[]"))
                if message_id not in ids:
                    ids.append(message_id)
                existing_due = _parse(row["not_before"]) or now
                due = max(existing_due, debounce_at)
                connection.execute(
                    """
                    UPDATE chat_reply_jobs
                    SET message_ids = ?, planned = 0, status = 'queued',
                        last_received_at = ?, not_before = ?, updated_at = ?,
                        lease_until = NULL, last_error = NULL
                    WHERE id = ?
                    """,
                    (json.dumps(ids), _iso(now), _iso(due), _iso(now), row["id"]),
                )
                updated = connection.execute(
                    "SELECT * FROM chat_reply_jobs WHERE id = ?", (row["id"],)
                ).fetchone()
                return self._job(updated)

            job_id = uuid4().hex
            connection.execute(
                """
                INSERT INTO chat_reply_jobs (
                    id, session_id, user_id, agent_id, status, message_ids,
                    planned, first_received_at, last_received_at, not_before,
                    attempt_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, 0, ?, ?, ?, 0, ?, ?)
                """,
                (
                    job_id,
                    session_id,
                    user_id,
                    agent_id,
                    json.dumps([message_id]),
                    _iso(now),
                    _iso(now),
                    _iso(debounce_at),
                    _iso(now),
                    _iso(now),
                ),
            )
            created = connection.execute(
                "SELECT * FROM chat_reply_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            return self._job(created)

    def claim_due(self, *, lease_seconds: float = 180.0) -> ReplyJob | None:
        now = _utcnow()
        lease_until = now + timedelta(seconds=max(30.0, lease_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE chat_reply_jobs
                SET status = 'queued',
                    planned = CASE WHEN status = 'typing' THEN 1 ELSE 0 END,
                    lease_until = NULL,
                    not_before = ?, updated_at = ?,
                    last_error = COALESCE(last_error, 'worker lease expired')
                WHERE status IN ('planning', 'typing')
                  AND lease_until IS NOT NULL AND lease_until <= ?
                """,
                (_iso(now), _iso(now), _iso(now)),
            )
            row = connection.execute(
                """
                SELECT * FROM chat_reply_jobs
                WHERE status IN ('queued', 'retry_wait') AND not_before <= ?
                ORDER BY not_before ASC, created_at ASC LIMIT 1
                """,
                (_iso(now),),
            ).fetchone()
            if row is None:
                return None
            next_status = "typing" if bool(row["planned"]) else "planning"
            connection.execute(
                """
                UPDATE chat_reply_jobs
                SET status = ?, started_at = CASE WHEN ? = 'typing' THEN ? ELSE started_at END,
                    lease_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_status,
                    next_status,
                    _iso(now),
                    _iso(lease_until),
                    _iso(now),
                    row["id"],
                ),
            )
            claimed = connection.execute(
                "SELECT * FROM chat_reply_jobs WHERE id = ?", (row["id"],)
            ).fetchone()
            return self._job(claimed)

    def finish_planning(
        self,
        job_id: str,
        *,
        outcome: str,
        lane: str | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        now = _utcnow()
        with self._connect() as connection:
            if outcome == "silent":
                connection.execute(
                    """
                    UPDATE chat_reply_jobs
                    SET status = 'silent', completed_at = ?, lease_until = NULL,
                        updated_at = ? WHERE id = ?
                    """,
                    (_iso(now), _iso(now), job_id),
                )
                return
            due = now + timedelta(seconds=max(0.0, delay_seconds))
            connection.execute(
                """
                UPDATE chat_reply_jobs
                SET status = 'queued', planned = 1, lane = ?, not_before = ?,
                    lease_until = NULL, updated_at = ? WHERE id = ?
                """,
                (lane or "fast", _iso(due), _iso(now), job_id),
            )

    def complete(self, job_id: str) -> None:
        now = _utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE chat_reply_jobs
                SET status = 'replied', completed_at = ?, lease_until = NULL,
                    updated_at = ? WHERE id = ?
                """,
                (_iso(now), _iso(now), job_id),
            )

    def retry_or_fail(
        self,
        job_id: str,
        error: str,
        *,
        max_attempts: int,
        delay_seconds: float,
    ) -> str:
        now = _utcnow()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT attempt_count FROM chat_reply_jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if row is None:
                return "failed"
            attempts = int(row["attempt_count"] or 0) + 1
            status = "failed" if attempts >= max(1, max_attempts) else "retry_wait"
            due = now + timedelta(seconds=max(0.0, delay_seconds))
            connection.execute(
                """
                UPDATE chat_reply_jobs
                SET status = ?, attempt_count = ?, not_before = ?, planned = 1,
                    lease_until = NULL, last_error = ?, updated_at = ?,
                    completed_at = CASE WHEN ? = 'failed' THEN ? ELSE completed_at END
                WHERE id = ?
                """,
                (
                    status,
                    attempts,
                    _iso(due),
                    error[:1000],
                    _iso(now),
                    status,
                    _iso(now),
                    job_id,
                ),
            )
            return status

    def reply_state(self, session_id: str) -> str:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT status FROM chat_reply_jobs
                WHERE session_id = ? AND status IN ('queued', 'planning', 'typing', 'retry_wait')
                """,
                (session_id,),
            ).fetchall()
        states = {row["status"] for row in rows}
        if "typing" in states:
            return "typing"
        if states:
            return "queued"
        return "idle"

    def has_open(self, user_id: str, agent_id: str = "default") -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM chat_reply_jobs
                WHERE user_id = ? AND agent_id = ?
                  AND status IN ('queued', 'planning', 'typing', 'retry_wait')
                LIMIT 1
                """,
                (user_id, agent_id),
            ).fetchone()
        return row is not None

    def cancel_session(self, session_id: str) -> None:
        now = _iso(_utcnow())
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE chat_reply_jobs SET status = 'cancelled', completed_at = ?,
                    lease_until = NULL, updated_at = ?
                WHERE session_id = ? AND status IN ('queued', 'planning', 'typing', 'retry_wait')
                """,
                (now, now, session_id),
            )
            connection.execute(
                "DELETE FROM chat_client_commands WHERE session_id = ?", (session_id,)
            )

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            session_rows = connection.execute(
                "SELECT DISTINCT session_id FROM chat_reply_jobs "
                "WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            ).fetchall()
            for row in session_rows:
                connection.execute(
                    "DELETE FROM chat_client_commands WHERE session_id = ?",
                    (row["session_id"],),
                )
            connection.execute(
                "DELETE FROM chat_reply_jobs WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )

    def add_location_command(self, session_id: str, request_id: str, ttl_seconds: int = 12) -> None:
        now = _utcnow()
        payload = json.dumps({"request_id": request_id}, ensure_ascii=False)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO chat_client_commands
                    (id, session_id, command_type, payload, created_at, expires_at, delivered_at)
                VALUES (?, ?, 'request_location', ?, ?, ?, NULL)
                """,
                (
                    request_id,
                    session_id,
                    payload,
                    _iso(now),
                    _iso(now + timedelta(seconds=max(1, ttl_seconds))),
                ),
            )

    def pop_commands(self, session_id: str) -> list[dict]:
        now = _utcnow()
        result: list[dict] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM chat_client_commands
                WHERE session_id = ? AND delivered_at IS NULL AND expires_at > ?
                ORDER BY created_at ASC
                """,
                (session_id, _iso(now)),
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload"] or "{}")
                result.append({"id": row["id"], "type": row["command_type"], **payload})
            if rows:
                connection.executemany(
                    "UPDATE chat_client_commands SET delivered_at = ? WHERE id = ?",
                    [(_iso(now), row["id"]) for row in rows],
                )
            connection.execute(
                "DELETE FROM chat_client_commands WHERE expires_at <= ?", (_iso(now),)
            )
        return result

    def counts(self, user_id: str | None = None) -> dict[str, int]:
        query = "SELECT status, COUNT(*) AS count FROM chat_reply_jobs"
        params: tuple[str, ...] = ()
        if user_id is not None:
            query += " WHERE user_id = ?"
            params = (user_id,)
        query += " GROUP BY status"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return {row["status"]: int(row["count"]) for row in rows}
