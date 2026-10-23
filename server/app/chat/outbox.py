"""Durable, leased per-message delivery; undelivered text never enters history."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class ReplyOutbox:
    def __init__(self, path: Path) -> None:
        self.path = path
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS chat_reply_groups (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL, source_ids TEXT NOT NULL, emotion TEXT NOT NULL,
                    gap REAL NOT NULL, notified INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS chat_reply_parts (
                    id TEXT PRIMARY KEY, group_id TEXT NOT NULL, part_index INTEGER NOT NULL,
                    content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    due_at REAL NOT NULL, lease_until REAL, error TEXT,
                    UNIQUE(group_id,part_index));
                CREATE INDEX IF NOT EXISTS idx_chat_parts_due ON chat_reply_parts(status,due_at);
            """)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def exists(self, job_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM chat_reply_groups WHERE id=?", (job_id,)).fetchone() is not None

    def prepare(self, job, contents: list, emotion: dict, *, gap: float) -> bool:
        now = datetime.now(timezone.utc).timestamp()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT status FROM chat_reply_jobs WHERE id=?", (job.id,)).fetchone()
            if not current or current[0] != "typing":
                return False  # A new user message fenced the generation.
            if db.execute("SELECT 1 FROM chat_reply_groups WHERE id=?", (job.id,)).fetchone():
                return True
            db.execute("INSERT INTO chat_reply_groups VALUES(?,?,?,?,?,?,?,0)", (
                job.id, job.session_id, job.user_id, job.agent_id,
                json.dumps(job.message_ids), json.dumps(emotion, ensure_ascii=False), gap))
            for index, content in enumerate(contents):
                db.execute("INSERT INTO chat_reply_parts(id,group_id,part_index,content,due_at) VALUES(?,?,?,?,?)",
                           (uuid4().hex, job.id, index, json.dumps(content, ensure_ascii=False), now + index * gap))
            db.execute("UPDATE chat_reply_jobs SET status='delivering',lease_until=NULL WHERE id=?", (job.id,))
        return True

    def claim(self) -> dict | None:
        now = datetime.now(timezone.utc).timestamp()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE chat_reply_parts SET status='pending',lease_until=NULL "
                       "WHERE status='sending' AND lease_until<=?", (now,))
            row = db.execute("""
                SELECT p.*, g.session_id,g.user_id,g.agent_id,g.source_ids,g.emotion,g.gap,g.notified
                FROM chat_reply_parts p JOIN chat_reply_groups g ON p.group_id=g.id
                JOIN chat_reply_jobs j ON j.id=g.id
                WHERE p.status='pending' AND p.due_at<=? AND j.status='delivering'
                AND NOT EXISTS (SELECT 1 FROM chat_reply_parts earlier
                    WHERE earlier.group_id=p.group_id AND earlier.part_index<p.part_index
                    AND earlier.status NOT IN ('delivered','cancelled'))
                ORDER BY p.due_at,p.part_index LIMIT 1
            """, (now,)).fetchone()
            if row is None:
                return None
            db.execute("UPDATE chat_reply_parts SET status='sending',lease_until=? WHERE id=?", (now + 60, row["id"]))
            result = dict(row)
            result["content"] = json.loads(row["content"])
            result["source_ids"] = json.loads(row["source_ids"])
            result["emotion"] = json.loads(row["emotion"])
            result["part_count"] = db.execute("SELECT COUNT(*) FROM chat_reply_parts WHERE group_id=?", (row["group_id"],)).fetchone()[0]
            return result

    def active(self, part_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM chat_reply_parts p JOIN chat_reply_jobs j ON j.id=p.group_id "
                              "WHERE p.id=? AND p.status='sending' AND j.status='delivering'", (part_id,)).fetchone() is not None

    def delivered(self, part: dict) -> tuple[bool, bool]:
        """Return (group completed, first notification reserved)."""
        now = datetime.now(timezone.utc).timestamp()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE chat_reply_parts SET status='delivered',lease_until=NULL WHERE id=?", (part["id"],))
            db.execute("UPDATE chat_reply_parts SET due_at=? WHERE group_id=? AND part_index=? AND status='pending'",
                       (now + part["gap"], part["group_id"], part["part_index"] + 1))
            remaining = db.execute("SELECT 1 FROM chat_reply_parts WHERE group_id=? AND status IN ('pending','sending')",
                                   (part["group_id"],)).fetchone()
            if not remaining:
                db.execute("UPDATE chat_reply_jobs SET status='replied',completed_at=?,updated_at=? "
                           "WHERE id=? AND status='delivering'", (datetime.now(timezone.utc).isoformat(),
                            datetime.now(timezone.utc).isoformat(), part["group_id"]))
            notified = db.execute("UPDATE chat_reply_groups SET notified=1 WHERE id=? AND notified=0",
                                  (part["group_id"],)).rowcount == 1
        return not bool(remaining), notified

    def retry(self, part_id: str, error: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE chat_reply_parts SET status='pending',lease_until=NULL,due_at=?,error=? "
                       "WHERE id=? AND status='sending'", (datetime.now(timezone.utc).timestamp() + 3, error[:250], part_id))

    def skip(self, part: dict) -> None:
        with self.connect() as db:
            db.execute("UPDATE chat_reply_parts SET status='cancelled',lease_until=NULL WHERE id=?", (part["id"],))
            pending = db.execute("SELECT 1 FROM chat_reply_parts WHERE group_id=? AND status IN ('pending','sending')",
                                 (part["group_id"],)).fetchone()
            if not pending:
                db.execute("UPDATE chat_reply_jobs SET status='replied' WHERE id=? AND status='delivering'", (part["group_id"],))

    @staticmethod
    def interrupt(db, session_id: str) -> list[str]:
        """Caller owns the job-enqueue transaction. Transfer unanswered inputs."""
        rows = db.execute("SELECT id,message_ids,capabilities FROM chat_reply_jobs WHERE session_id=? "
                          "AND status IN ('planning','typing','delivering')", (session_id,)).fetchall()
        transfer = []
        for row in rows:
            if "message_sequence_v1" not in json.loads(row["capabilities"] or "[]"):
                continue
            sent = db.execute("SELECT 1 FROM chat_reply_parts WHERE group_id=? AND status='delivered'", (row["id"],)).fetchone()
            if not sent:
                transfer.extend(json.loads(row["message_ids"]))
            db.execute("UPDATE chat_reply_jobs SET status='cancelled',lease_until=NULL WHERE id=?", (row["id"],))
            db.execute("UPDATE chat_reply_parts SET status='cancelled',lease_until=NULL "
                       "WHERE group_id=? AND status IN ('pending','sending')", (row["id"],))
        return transfer
