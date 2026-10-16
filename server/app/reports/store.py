from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


class ReportStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS report_clients(user_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, version INTEGER NOT NULL, registered_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS weekly_reports(
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, week_start TEXT NOT NULL, week_end TEXT NOT NULL,
              timezone TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', html TEXT NOT NULL DEFAULT '',
              evidence_json TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
              lease_until REAL NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
              created_at REAL NOT NULL, updated_at REAL NOT NULL, error TEXT,
              notified_at REAL, delivery_started REAL, revision INTEGER NOT NULL DEFAULT 0,
              UNIQUE(user_id,week_start));
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def register(self, user_id, version):
        with self.connect() as db:
            db.execute('INSERT INTO report_clients(user_id,version,registered_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET version=excluded.version,registered_at=excluded.registered_at', (user_id, version, time.time()))

    def preferences(self, user_id):
        with self.connect() as db:
            row = db.execute('SELECT enabled,version FROM report_clients WHERE user_id=?', (user_id,)).fetchone()
        return {"enabled": bool(row["enabled"]) if row else True, "supported": bool(row and row["version"] >= 40)}

    def set_enabled(self, user_id, enabled):
        with self.connect() as db:
            db.execute('UPDATE report_clients SET enabled=? WHERE user_id=?', (int(enabled), user_id))

    def clients(self):
        with self.connect() as db:
            return [r[0] for r in db.execute('SELECT user_id FROM report_clients WHERE enabled=1 AND version>=40')]

    def queue(self, user_id, start, end, timezone_name, refresh=False):
        report_id = uuid5(NAMESPACE_URL, f'auri-weekly:{user_id}:{start}').hex
        now = time.time()
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO weekly_reports(id,user_id,week_start,week_end,timezone,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)', (report_id, user_id, start, end, timezone_name, 'queued', now, now))
            if refresh:
                db.execute("UPDATE weekly_reports SET status='queued',attempts=0,retry_at=0,error=NULL WHERE id=? AND status IN ('ready','failed','queued')", (report_id,))
        return self.get(user_id, report_id)

    def get(self, user_id, report_id):
        with self.connect() as db:
            row = db.execute('SELECT * FROM weekly_reports WHERE id=? AND user_id=?', (report_id, user_id)).fetchone()
        return dict(row) if row else None

    def list(self, user_id):
        with self.connect() as db:
            return [dict(r) for r in db.execute('SELECT id,week_start,week_end,timezone,status,summary,created_at,updated_at,error,revision FROM weekly_reports WHERE user_id=? ORDER BY week_start DESC LIMIT 52', (user_id,))]

    def pending(self):
        now = time.time()
        with self.connect() as db:
            db.execute("UPDATE weekly_reports SET status='failed',error='生成中断，请重试。' WHERE status='generating' AND lease_until<? AND attempts>=3", (now,))
            return [dict(r) for r in db.execute("SELECT * FROM weekly_reports WHERE attempts<3 AND retry_at<=? AND (status='queued' OR (status='generating' AND lease_until<?)) ORDER BY created_at LIMIT 20", (now, now))]

    def claim(self, report_id):
        now = time.time()
        with self.connect() as db:
            return db.execute("UPDATE weekly_reports SET status='generating',attempts=attempts+1,lease_until=?,error=NULL WHERE id=? AND attempts<3 AND retry_at<=? AND (status='queued' OR (status='generating' AND lease_until<?))", (now + 600, report_id, now, now)).rowcount == 1

    def complete(self, report_id, summary, document, evidence):
        with self.connect() as db:
            return db.execute("UPDATE weekly_reports SET status='ready',summary=?,html=?,evidence_json=?,updated_at=?,lease_until=0,error=NULL,revision=revision+1 WHERE id=? AND status='generating'", (summary, document, json.dumps(evidence, ensure_ascii=False), time.time(), report_id)).rowcount == 1

    def unchanged(self, report_id):
        with self.connect() as db:
            db.execute("UPDATE weekly_reports SET status='ready',lease_until=0,error=NULL WHERE id=? AND status='generating'", (report_id,))

    def fail(self, report_id, message, *, retryable=True):
        with self.connect() as db:
            db.execute("UPDATE weekly_reports SET status=CASE WHEN attempts>=3 OR ?=0 THEN 'failed' ELSE 'queued' END,retry_at=?,lease_until=0,error=?,updated_at=? WHERE id=?", (int(retryable), time.time() + 60 if retryable else 0, message, time.time(), report_id))

    def waiting_delivery(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM weekly_reports WHERE status='ready' AND notified_at IS NULL ORDER BY created_at LIMIT 30")]

    def begin_delivery(self, report_id):
        with self.connect() as db:
            row = db.execute('SELECT delivery_started,notified_at FROM weekly_reports WHERE id=?', (report_id,)).fetchone()
            if row is None or row['notified_at'] is not None:
                return None
            first = row['delivery_started'] is None
            db.execute('UPDATE weekly_reports SET delivery_started=COALESCE(delivery_started,?) WHERE id=?', (time.time(), report_id))
            return first

    def delivered(self, report_id):
        with self.connect() as db:
            db.execute('UPDATE weekly_reports SET notified_at=? WHERE id=?', (time.time(), report_id))

    def delete_user(self, user_id):
        with self.connect() as db:
            db.execute('DELETE FROM weekly_reports WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM report_clients WHERE user_id=?', (user_id,))
