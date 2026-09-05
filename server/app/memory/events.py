from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from app.memory.models import OriginClass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class EventStatus(str, Enum):
    planned = "planned"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class TimePrecision(str, Enum):
    minute = "minute"
    hour = "hour"
    day = "day"
    week = "week"
    month = "month"
    unknown = "unknown"


class EventRelationType(str, Enum):
    before = "before"
    after = "after"
    during = "during"
    caused_by = "caused_by"
    follows = "follows"
    corrects = "corrects"


class EventDetailCandidate(BaseModel):
    detail_key: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=1000)
    salience: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    protected: bool = False


class EventRelationCandidate(BaseModel):
    target_event_key: str = Field(min_length=1, max_length=200)
    relation: EventRelationType


class EventCandidate(BaseModel):
    event_key: str = Field(min_length=1, max_length=200)
    thread_key: str | None = Field(default=None, max_length=200)
    kind: str = Field(default="life_event", min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=240)
    core_summary: str = Field(min_length=1, max_length=1500)
    status: EventStatus = EventStatus.completed
    occurred_start: datetime | None = None
    occurred_end: datetime | None = None
    time_precision: TimePrecision = TimePrecision.unknown
    timezone: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=500)
    current_until: datetime | None = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    details: list[EventDetailCandidate] = Field(default_factory=list)
    relations: list[EventRelationCandidate] = Field(default_factory=list)
    corrects_event_key: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _validate_time_range(self) -> "EventCandidate":
        if self.occurred_start and self.occurred_end:
            if _as_aware(self.occurred_end) < _as_aware(self.occurred_start):
                raise ValueError("occurred_end must not be before occurred_start")
        return self


class EventDetail(BaseModel):
    event_id: str
    detail_key: str
    content: str
    salience: float
    confidence: float
    protected: bool = False
    observed_at: datetime
    last_reinforced_at: datetime
    forgotten_at: datetime | None = None


class EventRelation(BaseModel):
    source_event_id: str
    target_event_id: str
    relation: EventRelationType
    created_at: datetime


class EventRecord(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    agent_id: str = "default"
    event_key: str
    thread_key: str | None = None
    kind: str
    title: str
    core_summary: str
    status: EventStatus
    occurred_start: datetime | None = None
    occurred_end: datetime | None = None
    time_precision: TimePrecision = TimePrecision.unknown
    timezone: str | None = None
    location: str | None = None
    current_until: datetime | None = None
    origin: OriginClass = OriginClass.owner
    importance: float = 0.5
    confidence: float = 1.0
    source_refs: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    last_reinforced_at: datetime = Field(default_factory=_utcnow)
    superseded_by: str | None = None
    details: list[EventDetail] = Field(default_factory=list)
    relations: list[EventRelation] = Field(default_factory=list)


class EventMemoryStore:
    """SQLite-backed canonical event timeline with independently decaying details."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    event_key TEXT NOT NULL,
                    thread_key TEXT,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    core_summary TEXT NOT NULL,
                    status TEXT NOT NULL,
                    occurred_start TEXT,
                    occurred_end TEXT,
                    time_precision TEXT NOT NULL,
                    timezone TEXT,
                    location TEXT,
                    current_until TEXT,
                    origin TEXT NOT NULL,
                    importance REAL NOT NULL,
                    confidence REAL NOT NULL,
                    source_refs TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_reinforced_at TEXT NOT NULL,
                    superseded_by TEXT,
                    UNIQUE(user_id, agent_id, event_key)
                );

                CREATE INDEX IF NOT EXISTS idx_events_scope_time
                    ON events(user_id, agent_id, occurred_start);
                CREATE INDEX IF NOT EXISTS idx_events_scope_status
                    ON events(user_id, agent_id, status);
                CREATE INDEX IF NOT EXISTS idx_events_scope_thread
                    ON events(user_id, agent_id, thread_key);

                CREATE TABLE IF NOT EXISTS event_details (
                    event_id TEXT NOT NULL,
                    detail_key TEXT NOT NULL,
                    content TEXT NOT NULL,
                    salience REAL NOT NULL,
                    confidence REAL NOT NULL,
                    protected INTEGER NOT NULL DEFAULT 0,
                    observed_at TEXT NOT NULL,
                    last_reinforced_at TEXT NOT NULL,
                    forgotten_at TEXT,
                    PRIMARY KEY(event_id, detail_key),
                    FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS event_relations (
                    source_event_id TEXT NOT NULL,
                    target_event_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(source_event_id, target_event_id, relation),
                    FOREIGN KEY(source_event_id) REFERENCES events(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_event_id) REFERENCES events(id) ON DELETE CASCADE
                );
                """
            )

    @staticmethod
    def _dt(value: str | None) -> datetime | None:
        return datetime.fromisoformat(value) if value else None

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return _as_aware(value).isoformat() if value is not None else None

    def _details(self, connection: sqlite3.Connection, event_id: str) -> list[EventDetail]:
        rows = connection.execute(
            "SELECT * FROM event_details WHERE event_id = ? ORDER BY salience DESC, detail_key",
            (event_id,),
        ).fetchall()
        return [
            EventDetail(
                event_id=row["event_id"],
                detail_key=row["detail_key"],
                content=row["content"],
                salience=float(row["salience"]),
                confidence=float(row["confidence"]),
                protected=bool(row["protected"]),
                observed_at=datetime.fromisoformat(row["observed_at"]),
                last_reinforced_at=datetime.fromisoformat(row["last_reinforced_at"]),
                forgotten_at=self._dt(row["forgotten_at"]),
            )
            for row in rows
        ]

    def _relations(
        self, connection: sqlite3.Connection, event_id: str
    ) -> list[EventRelation]:
        rows = connection.execute(
            "SELECT * FROM event_relations WHERE source_event_id = ? ORDER BY created_at",
            (event_id,),
        ).fetchall()
        return [
            EventRelation(
                source_event_id=row["source_event_id"],
                target_event_id=row["target_event_id"],
                relation=EventRelationType(row["relation"]),
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    def _record(self, connection: sqlite3.Connection, row: sqlite3.Row) -> EventRecord:
        return EventRecord(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            event_key=row["event_key"],
            thread_key=row["thread_key"],
            kind=row["kind"],
            title=row["title"],
            core_summary=row["core_summary"],
            status=EventStatus(row["status"]),
            occurred_start=self._dt(row["occurred_start"]),
            occurred_end=self._dt(row["occurred_end"]),
            time_precision=TimePrecision(row["time_precision"]),
            timezone=row["timezone"],
            location=row["location"],
            current_until=self._dt(row["current_until"]),
            origin=OriginClass(row["origin"]),
            importance=float(row["importance"]),
            confidence=float(row["confidence"]),
            source_refs=list(json.loads(row["source_refs"] or "[]")),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            last_reinforced_at=datetime.fromisoformat(row["last_reinforced_at"]),
            superseded_by=row["superseded_by"],
            details=self._details(connection, row["id"]),
            relations=self._relations(connection, row["id"]),
        )

    def get_by_key(
        self, user_id: str, agent_id: str, event_key: str
    ) -> EventRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE user_id = ? AND agent_id = ? AND event_key = ?",
                (user_id, agent_id, event_key),
            ).fetchone()
            return self._record(connection, row) if row is not None else None

    def get(self, event_id: str) -> EventRecord | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            return self._record(connection, row) if row is not None else None

    def upsert_candidate(
        self,
        *,
        user_id: str,
        agent_id: str,
        candidate: EventCandidate,
        origin: OriginClass,
        source_ref: str,
        now: datetime | None = None,
    ) -> EventRecord:
        current = _as_aware(now or _utcnow())
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM events WHERE user_id = ? AND agent_id = ? AND event_key = ?",
                (user_id, agent_id, candidate.event_key),
            ).fetchone()
            event_id = existing["id"] if existing is not None else uuid4().hex
            created_at = (
                datetime.fromisoformat(existing["created_at"])
                if existing is not None
                else current
            )
            old_refs = json.loads(existing["source_refs"] or "[]") if existing else []
            source_refs = list(dict.fromkeys([*old_refs, source_ref]))[-20:]
            time_precision = candidate.time_precision.value
            if (
                existing is not None
                and candidate.time_precision is TimePrecision.unknown
                and existing["time_precision"] != TimePrecision.unknown.value
            ):
                time_precision = existing["time_precision"]

            def choose(name: str, value: Any) -> Any:
                if value is not None or existing is None:
                    return value
                return existing[name]

            connection.execute(
                """
                INSERT INTO events (
                    id, user_id, agent_id, event_key, thread_key, kind, title,
                    core_summary, status, occurred_start, occurred_end, time_precision,
                    timezone, location, current_until, origin, importance, confidence,
                    source_refs, created_at, updated_at, last_reinforced_at, superseded_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, agent_id, event_key) DO UPDATE SET
                    thread_key = excluded.thread_key,
                    kind = excluded.kind,
                    title = excluded.title,
                    core_summary = excluded.core_summary,
                    status = excluded.status,
                    occurred_start = COALESCE(excluded.occurred_start, events.occurred_start),
                    occurred_end = COALESCE(excluded.occurred_end, events.occurred_end),
                    time_precision = excluded.time_precision,
                    timezone = COALESCE(excluded.timezone, events.timezone),
                    location = COALESCE(excluded.location, events.location),
                    current_until = COALESCE(excluded.current_until, events.current_until),
                    origin = excluded.origin,
                    importance = MAX(events.importance, excluded.importance),
                    confidence = MAX(events.confidence, excluded.confidence),
                    source_refs = excluded.source_refs,
                    updated_at = excluded.updated_at,
                    last_reinforced_at = excluded.last_reinforced_at,
                    superseded_by = NULL
                """,
                (
                    event_id,
                    user_id,
                    agent_id,
                    candidate.event_key,
                    choose("thread_key", candidate.thread_key),
                    candidate.kind,
                    candidate.title,
                    candidate.core_summary,
                    candidate.status.value,
                    self._iso(candidate.occurred_start),
                    self._iso(candidate.occurred_end),
                    time_precision,
                    choose("timezone", candidate.timezone),
                    choose("location", candidate.location),
                    self._iso(candidate.current_until),
                    origin.value,
                    candidate.importance,
                    candidate.confidence,
                    json.dumps(source_refs, ensure_ascii=False),
                    created_at.isoformat(),
                    current.isoformat(),
                    current.isoformat(),
                    None,
                ),
            )

            for detail in candidate.details:
                connection.execute(
                    """
                    INSERT INTO event_details (
                        event_id, detail_key, content, salience, confidence, protected,
                        observed_at, last_reinforced_at, forgotten_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                    ON CONFLICT(event_id, detail_key) DO UPDATE SET
                        content = excluded.content,
                        salience = MAX(event_details.salience, excluded.salience),
                        confidence = MAX(event_details.confidence, excluded.confidence),
                        protected = MAX(event_details.protected, excluded.protected),
                        last_reinforced_at = excluded.last_reinforced_at,
                        forgotten_at = NULL
                    """,
                    (
                        event_id,
                        detail.detail_key,
                        detail.content,
                        detail.salience,
                        detail.confidence,
                        int(detail.protected),
                        current.isoformat(),
                        current.isoformat(),
                    ),
                )

            relation_candidates = list(candidate.relations)
            if candidate.corrects_event_key:
                relation_candidates.append(
                    EventRelationCandidate(
                        target_event_key=candidate.corrects_event_key,
                        relation=EventRelationType.corrects,
                    )
                )
            for relation in relation_candidates:
                target = connection.execute(
                    "SELECT id FROM events WHERE user_id = ? AND agent_id = ? AND event_key = ?",
                    (user_id, agent_id, relation.target_event_key),
                ).fetchone()
                if target is None or target["id"] == event_id:
                    continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO event_relations
                        (source_event_id, target_event_id, relation, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (event_id, target["id"], relation.relation.value, current.isoformat()),
                )
                if relation.relation is EventRelationType.corrects:
                    connection.execute(
                        "UPDATE events SET superseded_by = ?, updated_at = ? WHERE id = ?",
                        (event_id, current.isoformat(), target["id"]),
                    )

            row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
            if row is None:
                raise RuntimeError("event upsert did not produce a row")
            return self._record(connection, row)

    def query(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        query: str | None = None,
        statuses: list[EventStatus] | None = None,
        include_superseded: bool = False,
        limit: int = 50,
    ) -> list[EventRecord]:
        sql = "SELECT * FROM events WHERE user_id = ? AND agent_id = ?"
        params: list[Any] = [user_id, agent_id]
        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({placeholders})"
            params.extend(status.value for status in statuses)
        if query:
            sql += (
                " AND (event_key LIKE ? OR thread_key LIKE ? OR kind LIKE ? "
                "OR title LIKE ? OR core_summary LIKE ? OR location LIKE ? "
                "OR EXISTS (SELECT 1 FROM event_details d "
                "WHERE d.event_id = events.id AND d.content LIKE ?))"
            )
            like = f"%{query}%"
            params.extend([like] * 7)
        sql += " ORDER BY COALESCE(occurred_start, updated_at) DESC, updated_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
            return [self._record(connection, row) for row in rows]

    def active(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[EventRecord]:
        current = _as_aware(now or _utcnow()).isoformat()
        sql = """
            SELECT * FROM events
            WHERE user_id = ? AND agent_id = ? AND superseded_by IS NULL
              AND status IN ('planned', 'in_progress')
              AND (current_until IS NULL OR current_until >= ?)
            ORDER BY COALESCE(occurred_start, updated_at) DESC
            LIMIT ?
        """
        with self._connect() as connection:
            rows = connection.execute(
                sql, (user_id, agent_id, current, max(1, min(limit, 100)))
            ).fetchall()
            return [self._record(connection, row) for row in rows]

    @staticmethod
    def detail_strength(
        detail: EventDetail,
        *,
        now: datetime | None = None,
        low_half_life_days: float = 14.0,
        normal_half_life_days: float = 45.0,
        high_half_life_days: float = 180.0,
    ) -> float:
        if detail.protected:
            return 1.0
        if detail.salience >= 0.75:
            half_life = high_half_life_days
        elif detail.salience >= 0.35:
            half_life = normal_half_life_days
        else:
            half_life = low_half_life_days
        current = _as_aware(now or _utcnow())
        reinforced = _as_aware(detail.last_reinforced_at)
        age_days = max(0.0, (current - reinforced).total_seconds() / 86400.0)
        base = 0.35 + 0.65 * detail.salience
        return max(0.0, min(1.0, base * math.pow(0.5, age_days / half_life)))

    def apply_decay(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
        threshold: float = 0.28,
        low_half_life_days: float = 14.0,
        normal_half_life_days: float = 45.0,
        high_half_life_days: float = 180.0,
    ) -> int:
        current = _as_aware(now or _utcnow())
        changed = 0
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*, e.status
                FROM event_details d
                JOIN events e ON e.id = d.event_id
                WHERE e.user_id = ? AND e.agent_id = ?
                  AND e.superseded_by IS NULL
                  AND d.forgotten_at IS NULL
                  AND d.protected = 0
                  AND e.status IN ('completed', 'cancelled')
                """,
                (user_id, agent_id),
            ).fetchall()
            for row in rows:
                detail = EventDetail(
                    event_id=row["event_id"],
                    detail_key=row["detail_key"],
                    content=row["content"],
                    salience=float(row["salience"]),
                    confidence=float(row["confidence"]),
                    protected=bool(row["protected"]),
                    observed_at=datetime.fromisoformat(row["observed_at"]),
                    last_reinforced_at=datetime.fromisoformat(row["last_reinforced_at"]),
                    forgotten_at=None,
                )
                strength = self.detail_strength(
                    detail,
                    now=current,
                    low_half_life_days=low_half_life_days,
                    normal_half_life_days=normal_half_life_days,
                    high_half_life_days=high_half_life_days,
                )
                if strength >= threshold:
                    continue
                connection.execute(
                    """
                    UPDATE event_details SET forgotten_at = ?
                    WHERE event_id = ? AND detail_key = ? AND forgotten_at IS NULL
                    """,
                    (current.isoformat(), detail.event_id, detail.detail_key),
                )
                changed += 1
        return changed

    def reinforce(self, event_id: str, now: datetime | None = None) -> None:
        current = _as_aware(now or _utcnow()).isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE events SET last_reinforced_at = ?, updated_at = ? WHERE id = ?",
                (current, current, event_id),
            )
            connection.execute(
                """
                UPDATE event_details
                SET last_reinforced_at = ?, forgotten_at = NULL
                WHERE event_id = ?
                """,
                (current, event_id),
            )

    def refresh_temporal_relations(
        self,
        user_id: str,
        agent_id: str,
        thread_key: str | None,
        *,
        now: datetime | None = None,
    ) -> None:
        """Link consecutive timestamped events in one storyline deterministically."""
        if not thread_key:
            return
        current = _as_aware(now or _utcnow()).isoformat()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, occurred_start, occurred_end
                FROM events
                WHERE user_id = ? AND agent_id = ? AND thread_key = ?
                  AND superseded_by IS NULL AND occurred_start IS NOT NULL
                ORDER BY occurred_start, COALESCE(occurred_end, occurred_start), created_at
                """,
                (user_id, agent_id, thread_key),
            ).fetchall()
            if rows:
                event_ids = [row["id"] for row in rows]
                placeholders = ",".join("?" for _ in event_ids)
                connection.execute(
                    f"""
                    DELETE FROM event_relations
                    WHERE relation IN ('before', 'after')
                      AND source_event_id IN ({placeholders})
                    """,
                    event_ids,
                )
            for earlier, later in zip(rows, rows[1:]):
                earlier_end = self._dt(earlier["occurred_end"] or earlier["occurred_start"])
                later_start = self._dt(later["occurred_start"])
                if earlier_end is None or later_start is None:
                    continue
                if _as_aware(earlier_end) > _as_aware(later_start):
                    continue
                connection.execute(
                    """
                    INSERT OR IGNORE INTO event_relations
                        (source_event_id, target_event_id, relation, created_at)
                    VALUES (?, ?, 'before', ?)
                    """,
                    (earlier["id"], later["id"], current),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO event_relations
                        (source_event_id, target_event_id, relation, created_at)
                    VALUES (?, ?, 'after', ?)
                    """,
                    (later["id"], earlier["id"], current),
                )

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM events WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )
