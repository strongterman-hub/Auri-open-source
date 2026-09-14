"""Durable extraction jobs store references, never another copy of chat bodies."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path


class EventJobStore:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS event_jobs (
                user_id TEXT NOT NULL, agent_id TEXT NOT NULL, session_id TEXT NOT NULL,
                message_id TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending',
                available_at REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                error TEXT, PRIMARY KEY(user_id,agent_id,session_id,message_id))""")

    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        return c

    def enqueue(self, user_id, agent_id, session_id, message_id, priority=0):
        with self.connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO event_jobs(user_id,agent_id,session_id,message_id,priority) VALUES(?,?,?,?,?)",
                (user_id, agent_id, session_id, message_id, priority),
            )

    def claim(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE event_jobs SET state='failed',error='lease_exhausted' WHERE state='running' AND lease_until<=? AND attempts>=3",
                (now,),
            )
            row = c.execute(
                """SELECT * FROM event_jobs WHERE
                attempts<3 AND ((state='pending' AND available_at<=?) OR (state='running' AND lease_until<=?))
                ORDER BY priority DESC, rowid LIMIT 1""",
                (now, now),
            ).fetchone()
            if row is None:
                return None
            job = dict(row)
            c.execute(
                """UPDATE event_jobs SET state='running',attempts=attempts+1,lease_until=?
                WHERE user_id=? AND agent_id=? AND session_id=? AND message_id=?""",
                (now + 300, *self.key(job)),
            )
            job["attempts"] += 1
            return job

    @staticmethod
    def key(job):
        return tuple(
            job[k] for k in ("user_id", "agent_id", "session_id", "message_id")
        )

    def finish(self, job, error=None):
        failed = error is not None
        state = ("failed" if job["attempts"] >= 3 else "pending") if failed else "done"
        with self.connect() as c:
            c.execute(
                """UPDATE event_jobs SET state=?,error=?,available_at=?,lease_until=0
                WHERE user_id=? AND agent_id=? AND session_id=? AND message_id=?""",
                (
                    state,
                    error,
                    time.time() + min(900, 30 * 2 ** job["attempts"]),
                    *self.key(job),
                ),
            )

    def delete_user(self, user_id, agent_id):
        with self.connect() as c:
            c.execute(
                "DELETE FROM event_jobs WHERE user_id=? AND agent_id=?",
                (user_id, agent_id),
            )
