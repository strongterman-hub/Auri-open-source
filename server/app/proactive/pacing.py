from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProactivePacingStore:
    """Persistent per-user proactive pacing state.

    Lifetime counters are retained for reporting. Control state is separate:
    settled, exposed reply opportunities contribute weighted misses, while
    shares and messages with unknown exposure do not.
    """

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
                CREATE TABLE IF NOT EXISTS pacing (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    total_sent INTEGER NOT NULL DEFAULT 0,
                    total_replied INTEGER NOT NULL DEFAULT 0,
                    unreplied_streak INTEGER NOT NULL DEFAULT 0,
                    last_sent_at TEXT,
                    last_reply_at TEXT,
                    next_daily_due_at TEXT,
                    PRIMARY KEY (user_id, agent_id)
                )
                """
            )
            self._ensure_column(connection, "pacing", "next_daily_due_at", "TEXT")
            added_miss_weight = self._ensure_column(
                connection, "pacing", "miss_weight", "REAL NOT NULL DEFAULT 0"
            )
            added_opportunities = self._ensure_column(
                connection, "pacing", "opportunities", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(
                connection, "pacing", "mode", "TEXT NOT NULL DEFAULT 'normal'"
            )
            self._ensure_column(
                connection, "pacing", "rest_level", "INTEGER NOT NULL DEFAULT 0"
            )
            self._ensure_column(connection, "pacing", "next_probe_at", "TEXT")
            self._ensure_column(
                connection, "pacing", "initiative_preference", "TEXT"
            )
            self._ensure_column(connection, "pacing", "preference_updated_at", "TEXT")
            self._ensure_column(connection, "pacing", "last_settled_at", "TEXT")
            self._ensure_column(connection, "pacing", "quiet_until", "TEXT")
            if added_miss_weight:
                # Preserve the existing control signal without carrying the old
                # one-message grace hack into the new weighted model.
                connection.execute(
                    "UPDATE pacing SET miss_weight = MAX(0, unreplied_streak - 1)"
                )
            if added_opportunities:
                connection.execute(
                    "UPDATE pacing SET opportunities = total_sent"
                )

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        ddl: str,
    ) -> bool:
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            return True
        return False

    def _ensure_row(self, user_id: str, agent_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pacing
                    (user_id, agent_id, total_sent, total_replied, unreplied_streak)
                VALUES (?, ?, 0, 0, 0)
                ON CONFLICT(user_id, agent_id) DO NOTHING
                """,
                (user_id, agent_id),
            )

    def record_sent(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        conversation_intent: str | None = None,
    ) -> None:
        """Record delivery.

        ``conversation_intent=None`` retains legacy immediate unanswered
        accounting for the separate onboarding pacing store. Daily messages
        pass an explicit intent and are scored only after exposure settlement.
        """
        now = _utcnow().isoformat()
        self._ensure_row(user_id, agent_id)
        legacy_increment = 1 if conversation_intent is None else 0
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pacing
                SET total_sent = total_sent + 1,
                    unreplied_streak = unreplied_streak + ?,
                    last_sent_at = ?
                WHERE user_id = ? AND agent_id = ?
                """,
                (legacy_increment, now, user_id, agent_id),
            )

    def record_reply(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        count_opportunity: bool = False,
    ) -> None:
        now = _utcnow().isoformat()
        self._ensure_row(user_id, agent_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pacing
                SET total_replied = total_replied + 1,
                    unreplied_streak = 0,
                    miss_weight = 0,
                    opportunities = opportunities + ?,
                    last_reply_at = ?,
                    last_settled_at = ?,
                    mode = CASE WHEN mode = 'resting' THEN 'recovery' ELSE 'normal' END,
                    rest_level = CASE WHEN mode = 'resting' THEN 0 ELSE rest_level END,
                    next_probe_at = NULL
                WHERE user_id = ? AND agent_id = ?
                """,
                (int(count_opportunity), now, now, user_id, agent_id),
            )

    def record_settlement(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        miss_weight: float,
    ) -> None:
        """Apply one settled opportunity; zero weight means exposure was unknown."""
        self._ensure_row(user_id, agent_id)
        now = _utcnow().isoformat()
        opportunity = 1 if miss_weight > 0 else 0
        with self._connect() as connection:
            row = connection.execute(
                "SELECT miss_weight FROM pacing WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            ).fetchone()
            current_weight = float(row["miss_weight"] or 0.0) if row else 0.0
            updated_weight = current_weight + max(0.0, float(miss_weight))
            connection.execute(
                """
                UPDATE pacing
                SET miss_weight = ?,
                    opportunities = opportunities + ?,
                    unreplied_streak = ?,
                    last_settled_at = ?,
                    mode = CASE
                        WHEN ? > 0 AND mode != 'resting' THEN 'cooling'
                        ELSE mode
                    END
                WHERE user_id = ? AND agent_id = ?
                """,
                (
                    updated_weight,
                    opportunity,
                    int(math.ceil(updated_weight)),
                    now,
                    max(0.0, float(miss_weight)),
                    user_id,
                    agent_id,
                ),
            )

    @staticmethod
    def _rest_delay(level: int) -> timedelta:
        hours = {1: 12, 2: 24, 3: 72}.get(max(1, level), 24 * 7)
        return timedelta(hours=hours)

    def enter_resting(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
    ) -> None:
        self._ensure_row(user_id, agent_id)
        current = now or _utcnow()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT mode, rest_level, last_settled_at, last_sent_at FROM pacing "
                "WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            ).fetchone()
            if row is None or row["mode"] == "resting":
                return
            level = max(1, int(row["rest_level"] or 0) + 1)
            anchor_raw = row["last_settled_at"] or row["last_sent_at"]
            anchor = datetime.fromisoformat(anchor_raw) if anchor_raw else current
            if anchor.tzinfo is None:
                anchor = anchor.replace(tzinfo=timezone.utc)
            due = anchor + self._rest_delay(level)
            connection.execute(
                """
                UPDATE pacing
                SET mode = 'resting', rest_level = ?, next_probe_at = ?
                WHERE user_id = ? AND agent_id = ?
                """,
                (level, due.isoformat(), user_id, agent_id),
            )

    def finish_probe(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
    ) -> None:
        """Ensure a due resting account cannot evaluate again every scheduler tick."""
        current = now or _utcnow()
        self._ensure_row(user_id, agent_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT mode, rest_level FROM pacing WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            ).fetchone()
            if row is None or row["mode"] != "resting":
                return
            level = max(1, int(row["rest_level"] or 1))
            connection.execute(
                "UPDATE pacing SET next_probe_at = ? WHERE user_id = ? AND agent_id = ?",
                ((current + self._rest_delay(level)).isoformat(), user_id, agent_id),
            )

    def advance_rest_after_miss(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
    ) -> None:
        current = now or _utcnow()
        self._ensure_row(user_id, agent_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT rest_level FROM pacing WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            ).fetchone()
            level = max(1, int(row["rest_level"] or 1) + 1)
            connection.execute(
                """
                UPDATE pacing SET mode = 'resting', rest_level = ?, next_probe_at = ?
                WHERE user_id = ? AND agent_id = ?
                """,
                (
                    level,
                    (current + self._rest_delay(level)).isoformat(),
                    user_id,
                    agent_id,
                ),
            )

    def apply_preference(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        preference: str,
        now: datetime | None = None,
    ) -> None:
        current = now or _utcnow()
        self._ensure_row(user_id, agent_id)
        if preference == "want_more":
            mode = "recovery"
            miss_weight = 0.5
            due = current + timedelta(minutes=30)
            rest_level = 0
        elif preference == "want_less":
            mode = "cooling"
            state = self.get_state(user_id, agent_id) or {}
            miss_weight = max(0.5, float(state.get("miss_weight") or 0.0))
            due = current + timedelta(hours=12)
            rest_level = int(state.get("rest_level") or 0)
        else:
            return
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pacing
                SET initiative_preference = ?, preference_updated_at = ?, mode = ?,
                    miss_weight = ?, unreplied_streak = ?, rest_level = ?,
                    next_probe_at = NULL, next_daily_due_at = ?, quiet_until = NULL
                WHERE user_id = ? AND agent_id = ?
                """,
                (
                    preference,
                    current.isoformat(),
                    mode,
                    miss_weight,
                    int(math.ceil(miss_weight)),
                    rest_level,
                    due.isoformat(),
                    user_id,
                    agent_id,
                ),
            )

    def set_temporary_quiet(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        until: datetime,
    ) -> None:
        self._ensure_row(user_id, agent_id)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pacing
                SET mode = 'temporary_quiet', quiet_until = ?, next_daily_due_at = ?
                WHERE user_id = ? AND agent_id = ?
                """,
                (until.isoformat(), until.isoformat(), user_id, agent_id),
            )

    def clear_expired_quiet(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
    ) -> None:
        current = now or _utcnow()
        state = self.get_state(user_id, agent_id)
        if not state or state.get("mode") != "temporary_quiet":
            return
        raw = state.get("quiet_until")
        until = datetime.fromisoformat(raw) if raw else current
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if current < until:
            return
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pacing SET mode = 'normal', quiet_until = NULL
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            )

    def reset_control_state(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        preference: str = "want_more",
    ) -> None:
        """Repair control state while preserving lifetime sent/reply statistics."""
        self.apply_preference(user_id, agent_id, preference=preference)

    def record_daily_check(
        self,
        user_id: str,
        agent_id: str = "default",
        due_at: str | None = None,
    ) -> None:
        """Persist when this user should next be evaluated by the daily tick."""
        self._ensure_row(user_id, agent_id)
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE pacing
                SET next_daily_due_at = ?
                WHERE user_id = ? AND agent_id = ?
                """,
                (due_at, user_id, agent_id),
            )

    def get_state(self, user_id: str, agent_id: str = "default") -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT total_sent, total_replied, unreplied_streak,
                       last_sent_at, last_reply_at, next_daily_due_at,
                       miss_weight, opportunities, mode, rest_level,
                       next_probe_at, initiative_preference,
                       preference_updated_at, last_settled_at, quiet_until
                FROM pacing
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "total_sent": int(row["total_sent"]),
            "total_replied": int(row["total_replied"]),
            "unreplied_streak": int(row["unreplied_streak"]),
            "last_sent_at": row["last_sent_at"],
            "last_reply_at": row["last_reply_at"],
            "next_daily_due_at": row["next_daily_due_at"],
            "miss_weight": float(row["miss_weight"] or 0.0),
            "opportunities": int(row["opportunities"] or 0),
            "mode": row["mode"] or "normal",
            "rest_level": int(row["rest_level"] or 0),
            "next_probe_at": row["next_probe_at"],
            "initiative_preference": row["initiative_preference"],
            "preference_updated_at": row["preference_updated_at"],
            "last_settled_at": row["last_settled_at"],
            "quiet_until": row["quiet_until"],
        }

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM pacing WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )
