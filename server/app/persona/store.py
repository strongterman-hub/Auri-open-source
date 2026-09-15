from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.persona.models import (
    AgentNote,
    OpenLoop,
    PersonaOverrides,
    PortraitSettings,
    PortraitState,
    RelationshipState,
    UserPersonaSelection,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _json_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


class PersonaStore:
    """SQLite repository for Auri's relationship state and open loops.

    The store deliberately shares ``memory.db`` with the other memory-domain
    tables so account deletion, backup and integrity checks keep one path.
    """

    def __init__(
        self,
        path: Path,
        *,
        open_loop_ttl_hours: int = 24,
        important_open_loop_ttl_hours: int = 72,
    ) -> None:
        self.path = Path(path)
        self.open_loop_ttl_hours = max(1, int(open_loop_ttl_hours))
        self.important_open_loop_ttl_hours = max(
            self.open_loop_ttl_hours, int(important_open_loop_ttl_hours)
        )
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
                CREATE TABLE IF NOT EXISTS relationship_state (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    stage TEXT NOT NULL DEFAULT 'warming',
                    address TEXT,
                    tone TEXT NOT NULL DEFAULT 'casual',
                    shared_topics_json TEXT NOT NULL DEFAULT '[]',
                    boundaries_json TEXT NOT NULL DEFAULT '[]',
                    last_correction_at TEXT,
                    recent_correction_count INTEGER NOT NULL DEFAULT 0,
                    last_user_tone TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, agent_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS user_persona (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    preset_id TEXT NOT NULL,
                    overrides_json TEXT NOT NULL DEFAULT '{}',
                    presentation TEXT,
                    selected_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, agent_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS open_loops (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    kind TEXT NOT NULL DEFAULT 'question',
                    topic_key TEXT,
                    summary TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'chat',
                    source_ref TEXT,
                    asked_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    closed_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_open_loops_scope_status
                ON open_loops(user_id, agent_id, status, expires_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_notes (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    kind TEXT NOT NULL DEFAULT 'commitment',
                    summary TEXT NOT NULL DEFAULT '',
                    source_ref TEXT,
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                )
                """
            )
            # Idempotent column add for databases created before the portrait work.
            persona_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(user_persona)")
            }
            if "presentation" not in persona_columns:
                connection.execute(
                    "ALTER TABLE user_persona ADD COLUMN presentation TEXT"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS portrait_state (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    variant TEXT NOT NULL,
                    presentation TEXT NOT NULL,
                    time_slot TEXT NOT NULL,
                    mood TEXT NOT NULL,
                    mood_hint TEXT,
                    mood_hint_at TEXT,
                    stage TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    signals_json TEXT NOT NULL DEFAULT '{}',
                    resolved_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, agent_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS portrait_settings (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    smart_background_enabled INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, agent_id)
                )
                """
            )

    def get_relationship_state(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> RelationshipState | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM relationship_state
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            ).fetchone()
        if row is None:
            return None
        return RelationshipState(
            stage=row["stage"] or "warming",
            address=row["address"],
            tone=row["tone"] or "casual",
            shared_topics=_json_list(row["shared_topics_json"]),
            boundaries=_json_list(row["boundaries_json"]),
            last_correction_at=_parse_dt(row["last_correction_at"]),
            recent_correction_count=int(row["recent_correction_count"] or 0),
            last_user_tone=row["last_user_tone"],
            updated_at=_parse_dt(row["updated_at"]) or _utcnow(),
        )

    def save_relationship_state(
        self,
        user_id: str,
        agent_id: str,
        state: RelationshipState,
    ) -> None:
        now = _utcnow().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO relationship_state
                    (user_id, agent_id, stage, address, tone, shared_topics_json,
                     boundaries_json, last_correction_at, recent_correction_count,
                     last_user_tone, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, agent_id) DO UPDATE SET
                    stage = excluded.stage,
                    address = excluded.address,
                    tone = excluded.tone,
                    shared_topics_json = excluded.shared_topics_json,
                    boundaries_json = excluded.boundaries_json,
                    last_correction_at = excluded.last_correction_at,
                    recent_correction_count = excluded.recent_correction_count,
                    last_user_tone = excluded.last_user_tone,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    agent_id,
                    state.stage,
                    state.address,
                    state.tone,
                    json.dumps(state.shared_topics[-8:], ensure_ascii=False),
                    json.dumps(state.boundaries[-12:], ensure_ascii=False),
                    state.last_correction_at.isoformat()
                    if state.last_correction_at
                    else None,
                    int(state.recent_correction_count or 0),
                    state.last_user_tone,
                    now,
                    now,
                ),
            )

    def get_user_persona(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> UserPersonaSelection | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM user_persona
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            ).fetchone()
        if row is None:
            return None
        try:
            overrides_payload = json.loads(row["overrides_json"] or "{}")
        except (TypeError, ValueError):
            overrides_payload = {}
        if not overrides_payload.get("presentation") and row["presentation"]:
            overrides_payload["presentation"] = row["presentation"]
        return UserPersonaSelection(
            preset_id=row["preset_id"],
            overrides=PersonaOverrides(**overrides_payload),
            selected_at=_parse_dt(row["selected_at"]) or _utcnow(),
            updated_at=_parse_dt(row["updated_at"]) or _utcnow(),
        )

    def set_user_persona(
        self,
        user_id: str,
        agent_id: str,
        selection: UserPersonaSelection,
    ) -> None:
        now = _utcnow().isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO user_persona
                    (user_id, agent_id, preset_id, overrides_json, presentation,
                     selected_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, agent_id) DO UPDATE SET
                    preset_id = excluded.preset_id,
                    overrides_json = excluded.overrides_json,
                    presentation = excluded.presentation,
                    selected_at = excluded.selected_at,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    agent_id,
                    selection.preset_id,
                    json.dumps(
                        selection.overrides.model_dump(exclude_none=True),
                        ensure_ascii=False,
                    ),
                    selection.overrides.presentation,
                    selection.selected_at.isoformat(),
                    now,
                ),
            )

    def get_portrait_state(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> PortraitState | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portrait_state
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            ).fetchone()
        if row is None:
            return None
        try:
            signals = json.loads(row["signals_json"] or "{}")
        except (TypeError, ValueError):
            signals = {}
        return PortraitState(
            variant=row["variant"] or "day_gentle",
            presentation=row["presentation"] or "female",
            time_slot=row["time_slot"] or "day",
            mood=row["mood"] or "neutral",
            stage=row["stage"] or "warming",
            reason=row["reason"] or "",
            mood_hint=row["mood_hint"],
            mood_hint_at=_parse_dt(row["mood_hint_at"]),
            signals=signals if isinstance(signals, dict) else {},
            resolved_at=_parse_dt(row["resolved_at"]) or _utcnow(),
        )

    def save_portrait_state(
        self,
        user_id: str,
        agent_id: str,
        state: PortraitState,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO portrait_state
                    (user_id, agent_id, variant, presentation, time_slot, mood,
                     mood_hint, mood_hint_at, stage, reason, signals_json,
                     resolved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, agent_id) DO UPDATE SET
                    variant = excluded.variant,
                    presentation = excluded.presentation,
                    time_slot = excluded.time_slot,
                    mood = excluded.mood,
                    mood_hint = excluded.mood_hint,
                    mood_hint_at = excluded.mood_hint_at,
                    stage = excluded.stage,
                    reason = excluded.reason,
                    signals_json = excluded.signals_json,
                    resolved_at = excluded.resolved_at
                """,
                (
                    user_id,
                    agent_id,
                    state.variant,
                    state.presentation,
                    state.time_slot,
                    state.mood,
                    state.mood_hint,
                    state.mood_hint_at.isoformat() if state.mood_hint_at else None,
                    state.stage,
                    state.reason,
                    json.dumps(state.signals, ensure_ascii=False),
                    state.resolved_at.isoformat(),
                ),
            )

    def save_mood_hint(
        self,
        user_id: str,
        agent_id: str,
        mood: str,
        observed_at: datetime,
        *,
        presentation: str = "female",
    ) -> None:
        now = observed_at.isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE portrait_state
                SET mood_hint = ?, mood_hint_at = ?
                WHERE user_id = ? AND agent_id = ?
                """,
                (mood, now, user_id, agent_id),
            )
            if cursor.rowcount:
                return
            connection.execute(
                """
                INSERT OR IGNORE INTO portrait_state
                    (user_id, agent_id, variant, presentation, time_slot, mood,
                     mood_hint, mood_hint_at, stage, reason, signals_json,
                     resolved_at)
                VALUES (?, ?, 'day_gentle', ?, 'day', 'neutral', ?, ?, 'warming',
                        'mood_observed', '{}', ?)
                """,
                (user_id, agent_id, presentation, mood, now, now),
            )

    def get_portrait_settings(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> PortraitSettings:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM portrait_settings
                WHERE user_id = ? AND agent_id = ?
                """,
                (user_id, agent_id),
            ).fetchone()
        if row is None:
            return PortraitSettings()
        return PortraitSettings(
            smart_background_enabled=bool(row["smart_background_enabled"]),
            updated_at=_parse_dt(row["updated_at"]) or _utcnow(),
        )

    def set_portrait_settings(
        self,
        user_id: str,
        agent_id: str,
        settings: PortraitSettings,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO portrait_settings
                    (user_id, agent_id, smart_background_enabled, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id, agent_id) DO UPDATE SET
                    smart_background_enabled = excluded.smart_background_enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    agent_id,
                    1 if settings.smart_background_enabled else 0,
                    settings.updated_at.isoformat(),
                ),
            )

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            for table in (
                "relationship_state",
                "user_persona",
                "open_loops",
                "agent_notes",
                "portrait_state",
                "portrait_settings",
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE user_id = ? AND agent_id = ?",
                    (user_id, agent_id),
                )

    def add_open_loop(self, user_id: str, agent_id: str, loop: OpenLoop) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO open_loops
                    (id, user_id, agent_id, kind, topic_key, summary, source,
                     source_ref, asked_at, expires_at, status, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    loop.id,
                    user_id,
                    agent_id,
                    loop.kind,
                    loop.topic_key,
                    loop.summary[:160],
                    loop.source,
                    loop.source_ref,
                    loop.asked_at.isoformat(),
                    loop.expires_at.isoformat(),
                    loop.status,
                    loop.closed_at.isoformat() if loop.closed_at else None,
                ),
            )

    def list_open_loops(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
    ) -> list[OpenLoop]:
        current = (now or _utcnow()).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM open_loops
                WHERE user_id = ? AND agent_id = ? AND status = 'open'
                  AND expires_at > ?
                ORDER BY asked_at DESC
                """,
                (user_id, agent_id, current),
            ).fetchall()
        return [
            OpenLoop(
                id=row["id"],
                kind=row["kind"] or "question",
                topic_key=row["topic_key"],
                summary=row["summary"] or "",
                source=row["source"] or "chat",
                source_ref=row["source_ref"],
                asked_at=_parse_dt(row["asked_at"]) or _utcnow(),
                expires_at=_parse_dt(row["expires_at"]) or _utcnow(),
                status=row["status"] or "open",
                closed_at=_parse_dt(row["closed_at"]),
            )
            for row in rows
        ]

    def count_open_loops(self, user_id: str, agent_id: str = "default") -> int:
        return len(self.list_open_loops(user_id, agent_id))

    def close_latest_open_loop(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        status: str = "answered",
    ) -> bool:
        now = _utcnow().isoformat()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT id FROM open_loops
                WHERE user_id = ? AND agent_id = ? AND status = 'open'
                ORDER BY asked_at DESC LIMIT 1
                """,
                (user_id, agent_id),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                """
                UPDATE open_loops
                SET status = ?, closed_at = ?
                WHERE id = ? AND status = 'open'
                """,
                (status, now, row["id"]),
            )
        return True

    def close_open_loop_by_source_ref(
        self,
        user_id: str,
        agent_id: str,
        source_ref: str,
    ) -> bool:
        now = _utcnow().isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE open_loops
                SET status = 'answered', closed_at = ?
                WHERE user_id = ? AND agent_id = ? AND source_ref = ?
                  AND status = 'open'
                """,
                (now, user_id, agent_id, source_ref),
            )
        return cursor.rowcount > 0

    def expire_open_loops(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
    ) -> int:
        current = (now or _utcnow()).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE open_loops
                SET status = 'expired', closed_at = ?
                WHERE user_id = ? AND agent_id = ? AND status = 'open'
                  AND expires_at <= ?
                """,
                (current, user_id, agent_id, current),
            )
        return int(cursor.rowcount or 0)

    def add_agent_note(self, user_id: str, agent_id: str, note: AgentNote) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO agent_notes
                    (id, user_id, agent_id, kind, summary, source_ref,
                     created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    note.id,
                    user_id,
                    agent_id,
                    note.kind,
                    note.summary[:200],
                    note.source_ref,
                    note.created_at.isoformat(),
                    note.expires_at.isoformat() if note.expires_at else None,
                ),
            )

    def list_agent_notes(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        limit: int = 8,
    ) -> list[AgentNote]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM agent_notes
                WHERE user_id = ? AND agent_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, agent_id, max(1, min(limit, 50))),
            ).fetchall()
        return [
            AgentNote(
                id=row["id"],
                kind=row["kind"] or "commitment",
                summary=row["summary"] or "",
                source_ref=row["source_ref"],
                created_at=_parse_dt(row["created_at"]) or _utcnow(),
                expires_at=_parse_dt(row["expires_at"]),
            )
            for row in rows
        ]

    def open_loop_expiry(self, *, important: bool = False) -> datetime:
        hours = (
            self.important_open_loop_ttl_hours
            if important
            else self.open_loop_ttl_hours
        )
        return _utcnow() + timedelta(hours=hours)