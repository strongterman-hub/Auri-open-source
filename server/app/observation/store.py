from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from app.memory.models import OriginClass
from app.observation.models import Observation, ObservationSource


class ObservationStore:
    """SQLite-backed append-only observation store with simple structured search."""

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
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS observations (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    source TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    supersession_key TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_obs_scope
                    ON observations(user_id, agent_id, source, kind);
                CREATE INDEX IF NOT EXISTS idx_obs_time ON observations(observed_at);
                CREATE TABLE IF NOT EXISTS observation_migrations (
                    name TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                """
            )
            self._migrate_health_idempotency(connection)

    @staticmethod
    def _migrate_health_idempotency(connection: sqlite3.Connection) -> None:
        """Compact legacy health snapshots once before enforcing logical uniqueness."""
        name = "health_observation_idempotency_v1"
        applied = connection.execute(
            "SELECT 1 FROM observation_migrations WHERE name = ?", (name,)
        ).fetchone()
        if applied is not None:
            return
        connection.execute(
            """
            DELETE FROM observations
            WHERE source = 'health' AND kind <> 'sync'
              AND rowid NOT IN (
                  SELECT MAX(rowid)
                  FROM observations
                  WHERE source = 'health' AND kind <> 'sync'
                  GROUP BY user_id, agent_id, source, kind, observed_at
              )
            """
        )
        connection.execute(
            """
            DELETE FROM observations
            WHERE source = 'health' AND kind = 'sync'
              AND rowid NOT IN (
                  SELECT MAX(rowid)
                  FROM observations
                  WHERE source = 'health' AND kind = 'sync'
                  GROUP BY user_id, agent_id, source, kind
              )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_obs_health_logical
            ON observations(user_id, agent_id, source, kind, observed_at)
            WHERE source = 'health' AND kind <> 'sync'
            """
        )
        connection.execute(
            "INSERT INTO observation_migrations(name, applied_at) VALUES (?, datetime('now'))",
            (name,),
        )

    def add(self, observation: Observation) -> None:
        self.add_many([observation])

    def add_many(self, observations: list[Observation]) -> None:
        if not observations:
            return
        rows = [self._to_row(observation) for observation in observations]
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO observations
                    (id, user_id, agent_id, source, observed_at, origin, kind,
                     payload, supersession_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def delete_user(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM observations WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )

    @staticmethod
    def _to_row(observation: Observation) -> tuple:
        return (
            observation.id,
            observation.user_id,
            observation.agent_id,
            observation.source.value,
            observation.observed_at.isoformat(),
            observation.origin.value,
            observation.kind,
            json.dumps(observation.payload, ensure_ascii=False),
            observation.supersession_key,
        )

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Observation:
        return Observation(
            id=row["id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            source=ObservationSource(row["source"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            origin=OriginClass(row["origin"]),
            kind=row["kind"],
            payload=json.loads(row["payload"]),
            supersession_key=row["supersession_key"],
        )

    def query(
        self,
        user_id: str,
        agent_id: str = "default",
        source: ObservationSource | None = None,
        kind: str | None = None,
        from_day: str | None = None,
        to_day: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[Observation]:
        sql = "SELECT * FROM observations WHERE user_id = ? AND agent_id = ?"
        params: list[object] = [user_id, agent_id]
        if source is not None:
            sql += " AND source = ?"
            params.append(source.value if isinstance(source, ObservationSource) else source)
        if kind is not None:
            sql += " AND kind = ?"
            params.append(kind)
        if from_day is not None:
            sql += " AND substr(observed_at, 1, 10) >= ?"
            params.append(from_day)
        if to_day is not None:
            sql += " AND substr(observed_at, 1, 10) <= ?"
            params.append(to_day)
        if query is not None:
            sql += " AND (payload LIKE ? OR kind LIKE ? OR source LIKE ?)"
            like = f"%{query}%"
            params.extend([like, like, like])
        sql += " ORDER BY observed_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._from_row(row) for row in rows]
