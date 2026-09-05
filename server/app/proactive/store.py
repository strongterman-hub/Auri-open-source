from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.proactive.models import (
    ConversationIntent,
    EngagementState,
    ProactiveDecision,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProactiveStore:
    """SQLite-backed proactive ledger with a small process-local content cache.

    The durable ledger intentionally excludes message bodies. They already live
    in the session store and can be recovered by ``session_id`` + decision id.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, ProactiveDecision] = {}
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS proactive_decisions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    session_id TEXT,
                    phase TEXT NOT NULL,
                    category TEXT,
                    trigger_type TEXT NOT NULL,
                    trigger_source TEXT,
                    conversation_intent TEXT NOT NULL DEFAULT 'share',
                    should_message INTEGER NOT NULL DEFAULT 0,
                    decided_at TEXT NOT NULL,
                    delivery_channel TEXT NOT NULL DEFAULT 'none',
                    chat_persisted_at TEXT,
                    push_status TEXT,
                    push_attempted_at TEXT,
                    acknowledged INTEGER NOT NULL DEFAULT 0,
                    exposed_at TEXT,
                    engagement_state TEXT NOT NULL DEFAULT 'planned',
                    replied INTEGER NOT NULL DEFAULT 0,
                    replied_at TEXT,
                    settled_at TEXT,
                    context_snapshot_id TEXT,
                    decision_reason TEXT,
                    silence_reason TEXT,
                    insight_key TEXT,
                    actions_json TEXT NOT NULL DEFAULT '[]'
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_proactive_user_time "
                "ON proactive_decisions(user_id, agent_id, decided_at DESC)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_proactive_unsettled "
                "ON proactive_decisions(user_id, agent_id, settled_at, decided_at DESC)"
            )

    @staticmethod
    def _value(value: Any) -> Any:
        return value.value if hasattr(value, "value") else value

    @staticmethod
    def _dt(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    def _write(self, connection: sqlite3.Connection, decision: ProactiveDecision) -> None:
        connection.execute(
            """
            INSERT INTO proactive_decisions (
                id, user_id, agent_id, session_id, phase, category, trigger_type,
                trigger_source, conversation_intent, should_message, decided_at,
                delivery_channel, chat_persisted_at, push_status, push_attempted_at,
                acknowledged, exposed_at, engagement_state, replied, replied_at,
                settled_at, context_snapshot_id, decision_reason, silence_reason,
                insight_key, actions_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                session_id = excluded.session_id,
                delivery_channel = excluded.delivery_channel,
                chat_persisted_at = excluded.chat_persisted_at,
                push_status = excluded.push_status,
                push_attempted_at = excluded.push_attempted_at,
                acknowledged = excluded.acknowledged,
                exposed_at = excluded.exposed_at,
                engagement_state = excluded.engagement_state,
                replied = excluded.replied,
                replied_at = excluded.replied_at,
                settled_at = excluded.settled_at,
                actions_json = excluded.actions_json
            """,
            (
                decision.id,
                decision.user_id,
                decision.agent_id,
                decision.session_id,
                self._value(decision.phase),
                self._value(decision.category),
                self._value(decision.trigger_type),
                decision.trigger_source,
                self._value(decision.conversation_intent),
                int(decision.should_message),
                self._dt(decision.decided_at),
                self._value(decision.delivery_channel),
                self._dt(decision.chat_persisted_at),
                decision.push_status,
                self._dt(decision.push_attempted_at),
                int(decision.acknowledged),
                self._dt(decision.exposed_at),
                self._value(decision.engagement_state),
                int(decision.replied),
                self._dt(decision.replied_at),
                self._dt(decision.settled_at),
                decision.context_snapshot_id,
                decision.decision_reason,
                decision.silence_reason,
                decision.insight_key,
                json.dumps(decision.actions, ensure_ascii=False),
            ),
        )

    def add(self, decision: ProactiveDecision) -> ProactiveDecision:
        self._cache[decision.id] = decision
        with self._connect() as connection:
            self._write(connection, decision)
        return decision

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    def _from_row(self, row: sqlite3.Row) -> ProactiveDecision:
        cached = self._cache.get(row["id"])
        message = cached.message if cached is not None else None
        push_message = cached.push_message if cached is not None else None
        tool_calls = cached.tool_calls if cached is not None else []
        evidence_refs = cached.evidence_refs if cached is not None else []
        restored = ProactiveDecision(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            session_id=row["session_id"],
            trigger_type=row["trigger_type"],
            trigger_source=row["trigger_source"],
            should_message=bool(row["should_message"]),
            phase=row["phase"],
            category=row["category"],
            insight_key=row["insight_key"],
            message=message,
            push_message=push_message,
            context_snapshot_id=row["context_snapshot_id"],
            decision_reason=row["decision_reason"],
            silence_reason=row["silence_reason"],
            evidence_refs=evidence_refs,
            tool_calls=tool_calls,
            actions=json.loads(row["actions_json"] or "[]"),
            decided_at=self._parse_datetime(row["decided_at"]),
            conversation_intent=row["conversation_intent"],
            delivery_channel=row["delivery_channel"],
            chat_persisted_at=self._parse_datetime(row["chat_persisted_at"]),
            push_status=row["push_status"],
            push_attempted_at=self._parse_datetime(row["push_attempted_at"]),
            acknowledged=bool(row["acknowledged"]),
            exposed_at=self._parse_datetime(row["exposed_at"]),
            engagement_state=row["engagement_state"],
            replied=bool(row["replied"]),
            replied_at=self._parse_datetime(row["replied_at"]),
            settled_at=self._parse_datetime(row["settled_at"]),
        )
        if cached is None:
            return restored
        for field in type(restored).model_fields:
            if field in {"message", "push_message", "tool_calls", "evidence_refs"}:
                continue
            setattr(cached, field, getattr(restored, field))
        return cached

    def list(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        only_unacknowledged: bool = True,
    ) -> list[ProactiveDecision]:
        where = "user_id = ? AND agent_id = ?"
        if only_unacknowledged:
            where += " AND acknowledged = 0"
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM proactive_decisions WHERE {where} ORDER BY decided_at",
                (user_id, agent_id),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def acknowledge(
        self,
        user_id: str,
        agent_id: str,
        decision_ids: list[str],
    ) -> int:
        wanted = list(dict.fromkeys(decision_ids))
        if not wanted:
            return 0
        now = _utcnow()
        placeholders = ",".join("?" for _ in wanted)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM proactive_decisions WHERE user_id = ? AND agent_id = ? "
                f"AND id IN ({placeholders})",
                (user_id, agent_id, *wanted),
            ).fetchall()
            for row in rows:
                decision = self._from_row(row)
                decision.acknowledged = True
                if decision.exposed_at is None:
                    decision.exposed_at = now
                if decision.settled_at is None:
                    decision.engagement_state = EngagementState.exposed
                self._cache[decision.id] = decision
                self._write(connection, decision)
        return len(rows)

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            ids = connection.execute(
                "SELECT id FROM proactive_decisions WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            ).fetchall()
            connection.execute(
                "DELETE FROM proactive_decisions WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )
        for row in ids:
            self._cache.pop(row["id"], None)

    def get(
        self,
        user_id: str,
        agent_id: str,
        decision_id: str,
    ) -> ProactiveDecision | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM proactive_decisions "
                "WHERE user_id = ? AND agent_id = ? AND id = ?",
                (user_id, agent_id, decision_id),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def mark_replied(
        self,
        user_id: str,
        agent_id: str,
        decision_id: str,
    ) -> ProactiveDecision | None:
        decision = self.get(user_id, agent_id, decision_id)
        if decision is None:
            return None
        if not decision.replied:
            decision.replied = True
            decision.replied_at = _utcnow()
            decision.settled_at = decision.replied_at
            decision.engagement_state = EngagementState.replied
            self.add(decision)
        return decision

    def recent_unsettled(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        since: datetime,
        limit: int = 3,
    ) -> list[ProactiveDecision]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM proactive_decisions
                WHERE user_id = ? AND agent_id = ? AND should_message = 1
                  AND settled_at IS NULL AND conversation_intent != 'share'
                  AND decided_at >= ?
                ORDER BY decided_at DESC LIMIT ?
                """,
                (user_id, agent_id, since.isoformat(), max(1, limit)),
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def settle_expired(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        before: datetime,
    ) -> list[tuple[ProactiveDecision, float]]:
        """Settle reply-expected messages and return newly observed miss weights."""
        settled: list[tuple[ProactiveDecision, float]] = []
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM proactive_decisions
                WHERE user_id = ? AND agent_id = ? AND should_message = 1
                  AND settled_at IS NULL AND conversation_intent != 'share'
                  AND decided_at <= ?
                ORDER BY decided_at
                """,
                (user_id, agent_id, before.isoformat()),
            ).fetchall()
            now = _utcnow()
            for row in rows:
                decision = self._from_row(row)
                if decision.exposed_at is None:
                    decision.engagement_state = EngagementState.expired_unseen
                    weight = 0.0
                else:
                    decision.engagement_state = EngagementState.seen_no_reply
                    weight = (
                        1.0
                        if decision.conversation_intent is ConversationIntent.direct_question
                        else 0.5
                    )
                decision.settled_at = now
                self._cache[decision.id] = decision
                self._write(connection, decision)
                settled.append((decision, weight))
        return settled

    def outstanding_counts(
        self, user_id: str, agent_id: str = "default"
    ) -> tuple[int, int]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN conversation_intent = 'direct_question' THEN 1 ELSE 0 END)
                        AS direct_count,
                    COUNT(*) AS interactive_count
                FROM proactive_decisions
                WHERE user_id = ? AND agent_id = ? AND should_message = 1
                  AND settled_at IS NULL AND conversation_intent != 'share'
                """,
                (user_id, agent_id),
            ).fetchone()
        return int(row["direct_count"] or 0), int(row["interactive_count"] or 0)

    def count_today(self, user_id: str, agent_id: str = "default") -> int:
        today = _utcnow().date()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT decided_at FROM proactive_decisions
                WHERE user_id = ? AND agent_id = ? AND should_message = 1
                """,
                (user_id, agent_id),
            ).fetchall()
        return sum(
            1 for row in rows if datetime.fromisoformat(row["decided_at"]).date() == today
        )
