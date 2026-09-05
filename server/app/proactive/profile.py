from __future__ import annotations

import sqlite3
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# Mandatory profile slots asked during cold-start onboarding. These drive the
# onboarding completeness score and are asked one at a time until the profile
# reaches the configured threshold.
#
# A health goal is intentionally NOT one of these mandatory slots: it is an
# optional profile field. A brand-new user whose profile is still empty should
# not be pushed toward a health goal before they have shown any interest in
# health. Health goals are instead captured through the durable memory/user
# model when the user volunteers them.
PROFILE_SLOTS: tuple[str, ...] = (
    "name",
    "gender",
    "age",
    "occupation",
    "routine",
    "lifestyle",
    "communication",
    "environment",
    "intention",
)


# One-shot onboarding guide/action items. Unlike profile slots, these do not
# wait for a reply; they are "filled" the moment Auri delivers the guide and
# are not counted toward profile completeness.
GUIDE_SLOTS: tuple[str, ...] = (
    "capability_intro",
    "xiaomi_connect",
    "proactive_enable",
    "notification",
    "location",
    "autostart",
)


# Ordered onboarding tiers. The engine only exposes slots from the first tier
# that still has unfinished items, so new users are oriented (name + what Auri
# can do) before setup and profile questions open up.
ONBOARDING_TIERS: tuple[tuple[str, ...], ...] = (
    ("name", "capability_intro"),
    (
        "xiaomi_connect",
        "proactive_enable",
        "notification",
        "location",
        "autostart",
    ),
    (
        "gender",
        "age",
        "occupation",
        "routine",
        "lifestyle",
        "communication",
        "environment",
        "intention",
    ),
)


ALL_ONBOARDING_SLOTS: tuple[str, ...] = PROFILE_SLOTS + GUIDE_SLOTS
TOTAL_ONBOARDING_SLOTS: int = len(ALL_ONBOARDING_SLOTS)
DENSE_SETTLED_TARGET_SLOTS: int = math.ceil(TOTAL_ONBOARDING_SLOTS * 0.8)


def slot_kind(slot: str) -> str:
    return "guide" if slot in GUIDE_SLOTS else "profile"


def slot_tier(slot: str) -> int:
    for index, tier in enumerate(ONBOARDING_TIERS, start=1):
        if slot in tier:
            return index
    return len(ONBOARDING_TIERS)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProfileStore:
    """Tracks which onboarding profile slots have been filled and asked."""

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
                CREATE TABLE IF NOT EXISTS profile_slots (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    slot TEXT NOT NULL,
                    filled INTEGER NOT NULL DEFAULT 0,
                    filled_at TEXT,
                    last_asked_at TEXT,
                    PRIMARY KEY (user_id, agent_id, slot)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS onboarding (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    started_at TEXT NOT NULL,
                    questions_asked INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, agent_id)
                )
                """
            )
            self._ensure_column(connection, "onboarding", "pending_slot", "TEXT")
            self._ensure_column(
                connection, "onboarding", "last_onboarding_at", "TEXT"
            )
            self._ensure_column(
                connection,
                "profile_slots",
                "status",
                "TEXT NOT NULL DEFAULT 'pending'",
            )
            self._ensure_column(
                connection,
                "profile_slots",
                "defer_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._ensure_column(
                connection,
                "onboarding",
                "phase",
                "TEXT NOT NULL DEFAULT 'dense'",
            )
            self._ensure_column(
                connection,
                "onboarding",
                "dense_pending_count",
                "INTEGER NOT NULL DEFAULT 0",
            )
            self._backfill_statuses(connection)

    @staticmethod
    def _ensure_column(
        connection: sqlite3.Connection,
        table: str,
        column: str,
        ddl: str,
    ) -> None:
        columns = {
            row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _backfill_statuses(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            UPDATE profile_slots
            SET status = CASE
                WHEN status IS NULL AND filled = 1 THEN 'completed'
                ELSE 'pending'
            END
            WHERE status IS NULL
            """
        )
        connection.execute(
            """
            UPDATE profile_slots SET status = 'completed'
            WHERE filled = 1 AND status != 'completed'
            """
        )
        connection.execute(
            "UPDATE onboarding SET phase = 'dense' WHERE phase IS NULL"
        )
        connection.execute(
            "UPDATE onboarding SET dense_pending_count = 0 "
            "WHERE dense_pending_count IS NULL"
        )
        for row in connection.execute(
            "SELECT user_id, agent_id FROM onboarding"
        ).fetchall():
            completed = connection.execute(
                """
                SELECT COUNT(*) FROM profile_slots
                WHERE user_id = ? AND agent_id = ? AND status = 'completed'
                """,
                (row["user_id"], row["agent_id"]),
            ).fetchone()[0]
            settled_non_completed = connection.execute(
                """
                SELECT COUNT(*) FROM profile_slots
                WHERE user_id = ? AND agent_id = ?
                  AND status IN ('skipped', 'deferred')
                """,
                (row["user_id"], row["agent_id"]),
            ).fetchone()[0]
            if completed >= TOTAL_ONBOARDING_SLOTS:
                phase = "done"
            elif completed + settled_non_completed >= DENSE_SETTLED_TARGET_SLOTS:
                phase = "slow"
            else:
                phase = "dense"
            connection.execute(
                "UPDATE onboarding SET phase = ? WHERE user_id = ? AND agent_id = ?",
                (phase, row["user_id"], row["agent_id"]),
            )

    def _ensure_onboarding_row(
        self,
        connection: sqlite3.Connection,
        user_id: str,
        agent_id: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO onboarding
                (user_id, agent_id, started_at, questions_asked, phase)
            VALUES (?, ?, ?, 0, 'dense')
            ON CONFLICT(user_id, agent_id) DO NOTHING
            """,
            (user_id, agent_id, _utcnow().isoformat()),
        )

    def mark_slot_pending(
        self,
        user_id: str,
        agent_id: str,
        slot: str,
    ) -> None:
        if slot not in ALL_ONBOARDING_SLOTS:
            return
        now = _utcnow()
        with self._connect() as connection:
            self._ensure_onboarding_row(connection, user_id, agent_id)
            connection.execute(
                """
                INSERT INTO profile_slots
                    (user_id, agent_id, slot, filled, filled_at, last_asked_at, status)
                VALUES (?, ?, ?, 0, NULL, ?, 'awaiting_reply')
                ON CONFLICT(user_id, agent_id, slot) DO UPDATE SET
                    filled = 0,
                    filled_at = NULL,
                    last_asked_at = excluded.last_asked_at,
                    status = 'awaiting_reply'
                """,
                (user_id, agent_id, slot, now.isoformat()),
            )

    def mark_slot_completed(
        self,
        user_id: str,
        agent_id: str,
        slot: str,
    ) -> None:
        if slot not in ALL_ONBOARDING_SLOTS:
            return
        now = _utcnow()
        with self._connect() as connection:
            self._ensure_onboarding_row(connection, user_id, agent_id)
            connection.execute(
                """
                INSERT INTO profile_slots
                    (user_id, agent_id, slot, filled, filled_at, last_asked_at, status)
                VALUES (?, ?, ?, 1, ?, NULL, 'completed')
                ON CONFLICT(user_id, agent_id, slot) DO UPDATE SET
                    filled = 1,
                    filled_at = excluded.filled_at,
                    status = 'completed'
                """,
                (user_id, agent_id, slot, now.isoformat()),
            )
            connection.execute(
                """
                UPDATE onboarding
                SET pending_slot = NULL
                WHERE user_id = ? AND agent_id = ? AND pending_slot = ?
                """,
                (user_id, agent_id, slot),
            )

    def mark_slot_skipped(
        self,
        user_id: str,
        agent_id: str,
        slot: str,
    ) -> None:
        if slot not in ALL_ONBOARDING_SLOTS:
            return
        with self._connect() as connection:
            self._ensure_onboarding_row(connection, user_id, agent_id)
            connection.execute(
                """
                INSERT INTO profile_slots
                    (user_id, agent_id, slot, filled, filled_at, last_asked_at, status)
                VALUES (?, ?, ?, 0, NULL, NULL, 'skipped')
                ON CONFLICT(user_id, agent_id, slot) DO UPDATE SET
                    filled = 0,
                    filled_at = NULL,
                    status = 'skipped'
                """,
                (user_id, agent_id, slot),
            )

    def mark_slot_deferred(
        self,
        user_id: str,
        agent_id: str,
        slot: str,
    ) -> None:
        if slot not in ALL_ONBOARDING_SLOTS:
            return
        with self._connect() as connection:
            self._ensure_onboarding_row(connection, user_id, agent_id)
            connection.execute(
                """
                INSERT INTO profile_slots
                    (user_id, agent_id, slot, filled, filled_at, last_asked_at,
                     status, defer_count)
                VALUES (?, ?, ?, 0, NULL, ?, 'deferred', 1)
                ON CONFLICT(user_id, agent_id, slot) DO UPDATE SET
                    filled = 0,
                    filled_at = NULL,
                    status = 'deferred',
                    defer_count = profile_slots.defer_count + 1
                """,
                (user_id, agent_id, slot, _utcnow().isoformat()),
            )
            connection.execute(
                """
                UPDATE onboarding SET pending_slot = NULL
                WHERE user_id = ? AND agent_id = ? AND pending_slot = ?
                """,
                (user_id, agent_id, slot),
            )

    def completed_slots(self, user_id: str, agent_id: str = "default") -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT slot FROM profile_slots
                WHERE user_id = ? AND agent_id = ? AND status = 'completed'
                """,
                (user_id, agent_id),
            ).fetchall()
        return {row["slot"] for row in rows if row["slot"] in ALL_ONBOARDING_SLOTS}

    def skipped_slots(self, user_id: str, agent_id: str = "default") -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT slot FROM profile_slots
                WHERE user_id = ? AND agent_id = ? AND status = 'skipped'
                """,
                (user_id, agent_id),
            ).fetchall()
        return {row["slot"] for row in rows if row["slot"] in ALL_ONBOARDING_SLOTS}

    def pending_slots(self, user_id: str, agent_id: str = "default") -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT slot FROM profile_slots
                WHERE user_id = ? AND agent_id = ?
                  AND status IN ('pending', 'awaiting_reply')
                """,
                (user_id, agent_id),
            ).fetchall()
        return {row["slot"] for row in rows if row["slot"] in ALL_ONBOARDING_SLOTS}

    def deferred_slots(self, user_id: str, agent_id: str = "default") -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT slot FROM profile_slots
                WHERE user_id = ? AND agent_id = ? AND status = 'deferred'
                """,
                (user_id, agent_id),
            ).fetchall()
        return {row["slot"] for row in rows if row["slot"] in ALL_ONBOARDING_SLOTS}

    def settled_slots(self, user_id: str, agent_id: str = "default") -> set[str]:
        return (
            self.completed_slots(user_id, agent_id)
            | self.skipped_slots(user_id, agent_id)
            | self.deferred_slots(user_id, agent_id)
        )

    def completed_count(self, user_id: str, agent_id: str = "default") -> int:
        return len(self.completed_slots(user_id, agent_id))

    def skipped_count(self, user_id: str, agent_id: str = "default") -> int:
        return len(self.skipped_slots(user_id, agent_id))

    def deferred_count(self, user_id: str, agent_id: str = "default") -> int:
        return len(self.deferred_slots(user_id, agent_id))

    def settled_count(self, user_id: str, agent_id: str = "default") -> int:
        return len(self.settled_slots(user_id, agent_id))

    def completion_rate(self, user_id: str, agent_id: str = "default") -> float:
        return self.completed_count(user_id, agent_id) / TOTAL_ONBOARDING_SLOTS

    def settled_rate(self, user_id: str, agent_id: str = "default") -> float:
        return self.settled_count(user_id, agent_id) / TOTAL_ONBOARDING_SLOTS

    def settle_expired_slots(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        timeout_seconds: float,
        now: datetime | None = None,
    ) -> set[str]:
        """Move stale waiting slots to deferred and release dense outstanding."""
        current = now or _utcnow()
        cutoff = current.timestamp() - max(0.0, float(timeout_seconds))
        expired: set[str] = set()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT slot, last_asked_at FROM profile_slots
                WHERE user_id = ? AND agent_id = ?
                  AND status IN ('pending', 'awaiting_reply')
                """,
                (user_id, agent_id),
            ).fetchall()
            for row in rows:
                raw = row["last_asked_at"]
                if raw is None:
                    is_expired = True
                else:
                    asked_at = datetime.fromisoformat(raw)
                    if asked_at.tzinfo is None:
                        asked_at = asked_at.replace(tzinfo=timezone.utc)
                    is_expired = asked_at.timestamp() <= cutoff
                if not is_expired:
                    continue
                expired.add(row["slot"])
                connection.execute(
                    """
                    UPDATE profile_slots
                    SET status = 'deferred', defer_count = defer_count + 1
                    WHERE user_id = ? AND agent_id = ? AND slot = ?
                    """,
                    (user_id, agent_id, row["slot"]),
                )
            if expired:
                placeholders = ",".join("?" for _ in expired)
                connection.execute(
                    f"""
                    UPDATE onboarding
                    SET pending_slot = CASE
                            WHEN pending_slot IN ({placeholders}) THEN NULL
                            ELSE pending_slot
                        END,
                        dense_pending_count = (
                            SELECT COUNT(*) FROM profile_slots
                            WHERE user_id = ? AND agent_id = ?
                              AND status IN ('pending', 'awaiting_reply')
                        )
                    WHERE user_id = ? AND agent_id = ?
                    """,
                    (
                        *expired,
                        user_id,
                        agent_id,
                        user_id,
                        agent_id,
                    ),
                )
        return expired

    def phase(self, user_id: str, agent_id: str = "default") -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT phase FROM onboarding WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            ).fetchone()
        return row["phase"] if row is not None else "dense"

    def set_phase(self, user_id: str, agent_id: str, phase: str) -> None:
        if phase not in ("dense", "slow", "done"):
            return
        with self._connect() as connection:
            self._ensure_onboarding_row(connection, user_id, agent_id)
            connection.execute(
                "UPDATE onboarding SET phase = ? WHERE user_id = ? AND agent_id = ?",
                (phase, user_id, agent_id),
            )

    def dense_pending_count(self, user_id: str, agent_id: str = "default") -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS waiting FROM profile_slots
                WHERE user_id = ? AND agent_id = ?
                  AND status IN ('pending', 'awaiting_reply')
                """,
                (user_id, agent_id),
            ).fetchone()
        return int(row["waiting"]) if row is not None else 0

    def deferred_revisit_allowed(
        self,
        user_id: str,
        agent_id: str,
        slot: str,
        *,
        retry_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT status, defer_count, last_asked_at FROM profile_slots
                WHERE user_id = ? AND agent_id = ? AND slot = ?
                """,
                (user_id, agent_id, slot),
            ).fetchone()
        if row is None or row["status"] != "deferred":
            return True
        if int(row["defer_count"] or 0) < 2 or not row["last_asked_at"]:
            return True
        asked_at = datetime.fromisoformat(row["last_asked_at"])
        if asked_at.tzinfo is None:
            asked_at = asked_at.replace(tzinfo=timezone.utc)
        return ((now or _utcnow()) - asked_at).total_seconds() >= retry_seconds

    def increment_dense_pending(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> None:
        with self._connect() as connection:
            self._ensure_onboarding_row(connection, user_id, agent_id)
            connection.execute(
                "UPDATE onboarding SET dense_pending_count = dense_pending_count + 1 "
                "WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )

    def reset_dense_pending(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> None:
        with self._connect() as connection:
            self._ensure_onboarding_row(connection, user_id, agent_id)
            connection.execute(
                "UPDATE onboarding SET dense_pending_count = 0 "
                "WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )

    def decrement_dense_pending(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> None:
        with self._connect() as connection:
            self._ensure_onboarding_row(connection, user_id, agent_id)
            connection.execute(
                """
                UPDATE onboarding
                SET dense_pending_count = MAX(0, dense_pending_count - 1)
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            )

    def filled_slots(self, user_id: str, agent_id: str = "default") -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT slot FROM profile_slots
                WHERE user_id = ? AND agent_id = ? AND filled = 1
                """,
                (user_id, agent_id),
            ).fetchall()
        return {row["slot"] for row in rows if row["slot"] in PROFILE_SLOTS}

    def filled_guide_slots(self, user_id: str, agent_id: str = "default") -> set[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT slot FROM profile_slots
                WHERE user_id = ? AND agent_id = ? AND filled = 1
                """,
                (user_id, agent_id),
            ).fetchall()
        return {row["slot"] for row in rows if row["slot"] in GUIDE_SLOTS}

    def mark_guide_filled(
        self,
        user_id: str,
        agent_id: str,
        slot: str,
    ) -> None:
        if slot not in GUIDE_SLOTS:
            return
        now = _utcnow()
        with self._connect() as connection:
            self._ensure_onboarding_row(connection, user_id, agent_id)
            connection.execute(
                """
                INSERT INTO profile_slots
                    (user_id, agent_id, slot, filled, filled_at, last_asked_at, status)
                VALUES (?, ?, ?, 1, ?, NULL, 'completed')
                ON CONFLICT(user_id, agent_id, slot) DO UPDATE SET
                    filled = excluded.filled,
                    filled_at = excluded.filled_at,
                    status = 'completed'
                """,
                (user_id, agent_id, slot, now.isoformat()),
            )

    def mark_filled(
        self,
        user_id: str,
        agent_id: str,
        slot: str,
        filled: bool = True,
    ) -> None:
        if slot not in PROFILE_SLOTS:
            return
        filled_at = _utcnow().isoformat() if filled else None
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO profile_slots
                    (user_id, agent_id, slot, filled, filled_at, last_asked_at, status)
                VALUES (?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(user_id, agent_id, slot) DO UPDATE SET
                    filled = excluded.filled,
                    filled_at = excluded.filled_at,
                    status = excluded.status
                """,
                (
                    user_id,
                    agent_id,
                    slot,
                    int(filled),
                    filled_at,
                    "completed" if filled else "pending",
                ),
            )
            if filled:
                connection.execute(
                    """
                    UPDATE onboarding
                    SET pending_slot = NULL
                    WHERE user_id = ? AND agent_id = ? AND pending_slot = ?
                    """,
                    (user_id, agent_id, slot),
                )

    def last_asked_at(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT MAX(last_asked_at) AS last_asked_at
                FROM profile_slots
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            ).fetchone()
        if row is None or row["last_asked_at"] is None:
            return None
        return datetime.fromisoformat(row["last_asked_at"])

    def record_ask(self, user_id: str, agent_id: str, slot: str) -> None:
        if slot not in PROFILE_SLOTS:
            return
        now = _utcnow()
        with self._connect() as connection:
            self._ensure_onboarding_row(connection, user_id, agent_id)
            connection.execute(
                """
                INSERT INTO profile_slots
                    (user_id, agent_id, slot, filled, filled_at, last_asked_at, status)
                VALUES (?, ?, ?, 0, NULL, ?, 'awaiting_reply')
                ON CONFLICT(user_id, agent_id, slot) DO UPDATE SET
                    last_asked_at = excluded.last_asked_at,
                    filled = 0,
                    filled_at = NULL,
                    status = 'awaiting_reply'
                """,
                (user_id, agent_id, slot, now.isoformat()),
            )
            connection.execute(
                """
                UPDATE onboarding
                SET
                    questions_asked = onboarding.questions_asked + 1,
                    pending_slot = ?
                WHERE user_id = ? AND agent_id = ?
                """,
                (slot, user_id, agent_id),
            )

    def pending_slot(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT pending_slot
                FROM onboarding
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            ).fetchone()
        if row is None or row["pending_slot"] is None:
            return None
        pending = row["pending_slot"]
        return pending if pending in PROFILE_SLOTS else None

    def clear_pending_slot(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> None:
        """Clear a pending onboarding slot so the engine can ask the next field."""
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE onboarding
                SET pending_slot = NULL
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            )

    def slot_last_asked_at(
        self,
        user_id: str,
        agent_id: str,
        slot: str,
    ) -> datetime | None:
        if slot not in PROFILE_SLOTS:
            return None
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT last_asked_at
                FROM profile_slots
                WHERE user_id = ? AND agent_id = ? AND slot = ?
                """,
                (user_id, agent_id, slot),
            ).fetchone()
        if row is None or row["last_asked_at"] is None:
            return None
        return datetime.fromisoformat(row["last_asked_at"])

    def onboarding_state(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> tuple[datetime | None, int]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT started_at, questions_asked
                FROM onboarding
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            ).fetchone()
        if row is None:
            return None, 0
        started_at = (
            datetime.fromisoformat(row["started_at"])
            if row["started_at"]
            else None
        )
        return started_at, int(row["questions_asked"])

    def last_onboarding_at(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> datetime | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT last_onboarding_at
                FROM onboarding
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            ).fetchone()
        if row is None or row["last_onboarding_at"] is None:
            return None
        return datetime.fromisoformat(row["last_onboarding_at"])

    def mark_message_sent(self, user_id: str, agent_id: str = "default") -> None:
        now = _utcnow()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO onboarding
                    (user_id, agent_id, started_at, questions_asked, last_onboarding_at)
                VALUES (?, ?, ?, 0, ?)
                ON CONFLICT(user_id, agent_id) DO NOTHING
                """,
                (user_id, agent_id, now.isoformat(), now.isoformat()),
            )
            connection.execute(
                """
                UPDATE onboarding
                SET last_onboarding_at = ?
                WHERE user_id = ? AND agent_id = ?
                """,
                (now.isoformat(), user_id, agent_id),
            )

    def completeness(self, user_id: str, agent_id: str = "default") -> float:
        filled = len(self.filled_slots(user_id, agent_id))
        return filled / len(PROFILE_SLOTS)

    def apply_assessment(
        self,
        user_id: str,
        agent_id: str,
        assessment: dict[str, Any],
    ) -> None:
        for slot in assessment.get("filled") or []:
            if isinstance(slot, str) and slot in PROFILE_SLOTS:
                self.mark_filled(user_id, agent_id, slot, True)

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM profile_slots WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )
            connection.execute(
                "DELETE FROM onboarding WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )
