"""Small persistent character state; changes are applied only to delivered replies."""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.chat.expression import EMOTIONS


class EmotionStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS emotion_states (
                    user_id TEXT, agent_id TEXT, payload TEXT NOT NULL,
                    PRIMARY KEY(user_id, agent_id));
                CREATE TABLE IF NOT EXISTS emotion_events (
                    user_id TEXT, agent_id TEXT, event_id TEXT, sticker_id TEXT,
                    created_at REAL NOT NULL, PRIMARY KEY(user_id,agent_id,event_id));
                CREATE TABLE IF NOT EXISTS expression_preferences (
                    user_id TEXT, agent_id TEXT, continuous_messages INTEGER NOT NULL DEFAULT 1,
                    stickers INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(user_id,agent_id));
                CREATE INDEX IF NOT EXISTS idx_emotion_recent ON emotion_events(user_id,agent_id,created_at DESC);
            """)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def current(self, user_id: str, agent_id: str = "default", *, now: float | None = None) -> dict:
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        with self._connect() as db:
            row = db.execute("SELECT payload FROM emotion_states WHERE user_id=? AND agent_id=?",
                             (user_id, agent_id)).fetchone()
        state = json.loads(row[0]) if row else {"name": "calm", "intensity": 0.0, "reason": "", "updated_at": now}
        age = max(0, now - state["updated_at"])
        strength = float(state["intensity"]) * math.pow(0.5, age / 3600)
        if strength < 0.08:
            return {"name": "calm", "intensity": 0.0, "reason": "", "updated_at": state["updated_at"]}
        return {**state, "intensity": round(strength, 3)}

    def apply(self, user_id: str, agent_id: str, event_id: str, emotion: dict,
              sticker_id: str | None = None) -> bool:
        now = datetime.now(timezone.utc).timestamp()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            inserted = db.execute("INSERT OR IGNORE INTO emotion_events VALUES(?,?,?,?,?)",
                                  (user_id, agent_id, event_id, sticker_id, now)).rowcount
            if not inserted:
                return False
            if emotion.get("name") in EMOTIONS and emotion.get("reason"):
                old = self.current(user_id, agent_id, now=now)
                target = min(0.8, max(0.0, float(emotion.get("intensity", 0.2))))
                intensity = min(target, old["intensity"] + 0.35)
                state = {"name": emotion["name"], "intensity": intensity,
                         "reason": str(emotion["reason"])[:180], "updated_at": now}
                db.execute("INSERT OR REPLACE INTO emotion_states VALUES(?,?,?)",
                           (user_id, agent_id, json.dumps(state, ensure_ascii=False)))
        return True

    def recent_stickers(self, user_id: str, agent_id: str) -> list[str]:
        with self._connect() as db:
            rows = db.execute("SELECT sticker_id FROM emotion_events WHERE user_id=? AND agent_id=? "
                              "AND sticker_id IS NOT NULL ORDER BY created_at DESC LIMIT 3",
                              (user_id, agent_id)).fetchall()
        return [row[0] for row in rows]

    def preferences(self, user_id: str, agent_id: str = "default") -> dict:
        with self._connect() as db:
            row = db.execute("SELECT continuous_messages, stickers FROM expression_preferences "
                             "WHERE user_id=? AND agent_id=?", (user_id, agent_id)).fetchone()
        return {"continuous_messages": bool(row[0]) if row else True,
                "stickers": bool(row[1]) if row else True}

    def set_preferences(self, user_id: str, agent_id: str = "default", **changes) -> dict:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT continuous_messages,stickers FROM expression_preferences "
                             "WHERE user_id=? AND agent_id=?", (user_id, agent_id)).fetchone()
            values = {"continuous_messages": bool(row[0]) if row else True,
                      "stickers": bool(row[1]) if row else True}
            values.update({k: bool(v) for k, v in changes.items() if k in values and v is not None})
            db.execute("INSERT OR REPLACE INTO expression_preferences VALUES(?,?,?,?)",
                       (user_id, agent_id, int(values["continuous_messages"]), int(values["stickers"])))
        return values

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as db:
            for table in ("emotion_states", "emotion_events", "expression_preferences"):
                db.execute(f"DELETE FROM {table} WHERE user_id=? AND agent_id=?", (user_id, agent_id))
