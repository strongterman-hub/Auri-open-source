from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from app.schemas.health import (
    HealthMetricIn,
    HealthMetricOut,
    HealthSampleIn,
    HealthSampleOut,
)
from app.services.timezone_store import normalize_timezone, resolve_zoneinfo


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _local_day_start_utc(day: str, tz_name: str) -> str:
    tz = resolve_zoneinfo(tz_name)
    start = datetime.combine(date.fromisoformat(day), time.min, tzinfo=tz).astimezone(UTC)
    return _iso_utc(start)


def _local_day_bounds(from_day: str, to_day: str, tz_name: str) -> tuple[str, str]:
    """Return the UTC instants bounding ``[from_day, to_day]`` in a local zone."""
    tz = resolve_zoneinfo(tz_name)
    start = datetime.combine(date.fromisoformat(from_day), time.min, tzinfo=tz).astimezone(UTC)
    end_day = date.fromisoformat(to_day) + timedelta(days=1)
    end = datetime.combine(end_day, time.min, tzinfo=tz).astimezone(UTC)
    return _iso_utc(start), _iso_utc(end)


def _utc_iso_to_local_day(utc_iso: str, tz_name: str) -> str:
    tz = resolve_zoneinfo(tz_name)
    dt = datetime.fromisoformat(utc_iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(tz).date().isoformat()


class HealthStore:
    """SQLite-backed per-user health metric and sample store.

    Metrics are keyed by ``(user_id, metric_type, day)`` and samples by
    ``(user_id, metric_type, day, bucket_start)``. ``day`` is a user-local
    calendar date; ``day_start`` / ``bucket_start`` are UTC instants used for
    range queries so records can be re-attributed to a different local day when
    the user's timezone changes.
    """

    def __init__(self, root: Path, known_user_ids: list[str] | None = None) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "health.db"
        self._init_db()
        user_ids = known_user_ids if known_user_ids is not None else self._discover_user_ids()
        self._migrate_json(user_ids)
        self._backfill_day_start()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS health_metrics (
                    user_id TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    day TEXT NOT NULL,
                    day_start TEXT,
                    value1 REAL,
                    value2 REAL,
                    value3 REAL,
                    source TEXT,
                    source_updated_at INTEGER NOT NULL DEFAULT 0,
                    resolution_policy TEXT,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, metric_type, day)
                );

                CREATE TABLE IF NOT EXISTS health_samples (
                    user_id TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    day TEXT NOT NULL,
                    bucket_start TEXT NOT NULL,
                    bucket_end TEXT,
                    value1 REAL,
                    value2 REAL,
                    value3 REAL,
                    value4 TEXT,
                    source TEXT,
                    quality REAL,
                    metadata_json TEXT,
                    updated_at INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, metric_type, day, bucket_start)
                );

                CREATE INDEX IF NOT EXISTS idx_health_metrics_scope
                    ON health_metrics(user_id, metric_type, day);

                CREATE INDEX IF NOT EXISTS idx_health_samples_scope
                    ON health_samples(user_id, metric_type, day);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(health_metrics)").fetchall()
            }
            if "day_start" not in columns:
                connection.execute(
                    "ALTER TABLE health_metrics ADD COLUMN day_start TEXT"
                )
            for name, ddl in (
                ("source", "TEXT"),
                ("source_updated_at", "INTEGER NOT NULL DEFAULT 0"),
                ("resolution_policy", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE health_metrics ADD COLUMN {name} {ddl}"
                    )
            sample_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(health_samples)").fetchall()
            }
            for name, ddl in (
                ("source", "TEXT"),
                ("quality", "REAL"),
                ("metadata_json", "TEXT"),
            ):
                if name not in sample_columns:
                    connection.execute(
                        f"ALTER TABLE health_samples ADD COLUMN {name} {ddl}"
                    )

    def _backfill_day_start(self) -> None:
        """Interpret legacy ``day`` values in the old storage zone."""
        legacy_tz = "Asia/Shanghai"
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT user_id, metric_type, day FROM health_metrics "
                "WHERE day_start IS NULL OR day_start = ''"
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE health_metrics SET day_start = ? "
                    "WHERE user_id = ? AND metric_type = ? AND day = ?",
                    (
                        _local_day_start_utc(row["day"], legacy_tz),
                        row["user_id"],
                        row["metric_type"],
                        row["day"],
                    ),
                )

    def _discover_user_ids(self) -> list[str]:
        users_path = self.root.parent / "auth" / "users.json"
        try:
            payload = json.loads(users_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(payload, dict):
            return []
        return [str(key) for key in payload.keys()]

    def _load_json(self, path: Path) -> dict:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"metrics": {}, "samples": {}}
        if not isinstance(payload, dict):
            return {"metrics": {}, "samples": {}}
        payload.setdefault("metrics", {})
        payload.setdefault("samples", {})
        return payload

    def _migrate_json(self, known_user_ids: list[str]) -> None:
        for user_id in known_user_ids:
            digest = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
            path = self.root / f"{digest}.json"
            if not path.exists():
                continue

            payload = self._load_json(path)
            metrics_by_type = payload.get("metrics", {})
            samples_by_type = payload.get("samples", {})

            with self._connect() as connection:
                for metric_type, days in metrics_by_type.items():
                    if not isinstance(days, dict):
                        continue
                    for day, raw in days.items():
                        if not isinstance(raw, dict):
                            continue
                        connection.execute(
                            """
                            INSERT OR REPLACE INTO health_metrics
                                (user_id, metric_type, day, day_start, value1, value2, value3, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                user_id,
                                metric_type,
                                day,
                                _local_day_start_utc(day, "Asia/Shanghai"),
                                raw.get("value1"),
                                raw.get("value2"),
                                raw.get("value3"),
                                raw.get("updated_at", 0),
                            ),
                        )

                for sample_type, days in samples_by_type.items():
                    if not isinstance(days, dict):
                        continue
                    for day, buckets in days.items():
                        if not isinstance(buckets, dict):
                            continue
                        for raw in buckets.values():
                            if not isinstance(raw, dict):
                                continue
                            connection.execute(
                                """
                                INSERT OR REPLACE INTO health_samples
                                    (user_id, metric_type, day, bucket_start, bucket_end,
                                     value1, value2, value3, value4, source, quality,
                                     metadata_json, updated_at)
                                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    user_id,
                                    sample_type,
                                    day,
                                    raw.get("bucket_start"),
                                    raw.get("bucket_end"),
                                    raw.get("value1"),
                                    raw.get("value2"),
                                    raw.get("value3"),
                                    raw.get("value4"),
                                    raw.get("source"),
                                    raw.get("quality"),
                                    json.dumps(raw.get("metadata"), ensure_ascii=False)
                                    if raw.get("metadata") is not None
                                    else None,
                                    raw.get("updated_at", 0),
                                ),
                            )

            backup = path.with_suffix(path.suffix + ".migrated")
            path.replace(backup)

    def sync(
        self,
        user_id: str,
        from_day: str,
        to_day: str,
        metric_types: list[str],
        metrics: list[HealthMetricIn],
        samples: list[HealthSampleIn],
        sample_types: list[str] | None = None,
        tz: str = "Asia/Shanghai",
    ) -> int:
        tz_name = normalize_timezone(tz)
        start_iso, end_iso = _local_day_bounds(from_day, to_day, tz_name)
        sample_types_to_clear = (
            sample_types if sample_types is not None else {sample.metric_type for sample in samples}
        )

        metric_rows = []
        for metric in metrics:
            day_start = metric.day_start or _local_day_start_utc(metric.day, tz_name)
            if not (start_iso <= day_start < end_iso):
                continue
            metric_rows.append(
                (
                    user_id,
                    metric.metric_type,
                    metric.day,
                    day_start,
                    metric.value1,
                    metric.value2,
                    metric.value3,
                    metric.source,
                    metric.source_updated_at,
                    metric.resolution_policy,
                    metric.updated_at,
                )
            )

        sample_rows = []
        for sample in samples:
            if sample.metric_type in {"SLEEP_SESSION", "SLEEP_HRV"}:
                in_range = start_iso < sample.bucket_end <= end_iso
            else:
                in_range = start_iso <= sample.bucket_start < end_iso
            if not in_range:
                continue
            sample_rows.append(
                (
                    user_id,
                    sample.metric_type,
                    sample.day,
                    sample.bucket_start,
                    sample.bucket_end,
                    sample.value1,
                    sample.value2,
                    sample.value3,
                    sample.value4,
                    sample.source,
                    sample.quality,
                    json.dumps(sample.metadata, ensure_ascii=False, sort_keys=True)
                    if sample.metadata is not None
                    else None,
                    sample.updated_at,
                )
            )

        with self._connect() as connection:
            for metric_type in metric_types:
                connection.execute(
                    "DELETE FROM health_metrics WHERE user_id = ? AND metric_type = ? "
                    "AND day_start >= ? AND day_start < ?",
                    (user_id, metric_type, start_iso, end_iso),
                )
            if metric_rows:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO health_metrics
                        (user_id, metric_type, day, day_start, value1, value2, value3,
                         source, source_updated_at, resolution_policy, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    metric_rows,
                )

            for sample_type in sample_types_to_clear:
                if sample_type in {"SLEEP_SESSION", "SLEEP_HRV"}:
                    connection.execute(
                        "DELETE FROM health_samples WHERE user_id = ? AND metric_type = ? "
                        "AND bucket_end > ? AND bucket_end <= ?",
                        (user_id, sample_type, start_iso, end_iso),
                    )
                else:
                    connection.execute(
                        "DELETE FROM health_samples WHERE user_id = ? AND metric_type = ? "
                        "AND bucket_start >= ? AND bucket_start < ?",
                        (user_id, sample_type, start_iso, end_iso),
                    )
            if sample_rows:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO health_samples
                        (user_id, metric_type, day, bucket_start, bucket_end,
                         value1, value2, value3, value4, source, quality,
                         metadata_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    sample_rows,
                )

        return len(metrics) + len(samples)

    def get_metrics(
        self,
        user_id: str,
        from_day: str,
        to_day: str,
        tz: str = "Asia/Shanghai",
    ) -> tuple[list[HealthMetricOut], list[HealthSampleOut]]:
        tz_name = normalize_timezone(tz)
        start_iso, end_iso = _local_day_bounds(from_day, to_day, tz_name)
        with self._connect() as connection:
            metric_rows = connection.execute(
                "SELECT * FROM health_metrics WHERE user_id = ? "
                "AND day_start >= ? AND day_start < ? ORDER BY metric_type, day_start",
                (user_id, start_iso, end_iso),
            ).fetchall()
            sample_rows = connection.execute(
                "SELECT * FROM health_samples WHERE user_id = ? AND ("
                "(metric_type IN ('SLEEP_SESSION', 'SLEEP_HRV') "
                "AND bucket_end > ? AND bucket_end <= ?) OR "
                "(metric_type NOT IN ('SLEEP_SESSION', 'SLEEP_HRV') "
                "AND bucket_start >= ? AND bucket_start < ?)) "
                "ORDER BY metric_type, bucket_start",
                (user_id, start_iso, end_iso, start_iso, end_iso),
            ).fetchall()

        metrics = [
            HealthMetricOut(
                metric_type=row["metric_type"],
                day=_utc_iso_to_local_day(row["day_start"], tz_name),
                day_start=row["day_start"],
                value1=row["value1"],
                value2=row["value2"],
                value3=row["value3"],
                source=row["source"],
                source_updated_at=row["source_updated_at"],
                resolution_policy=row["resolution_policy"],
                updated_at=row["updated_at"],
            )
            for row in metric_rows
        ]
        samples = [
            HealthSampleOut(
                metric_type=row["metric_type"],
                day=_utc_iso_to_local_day(
                    row["bucket_end"]
                    if row["metric_type"] in {"SLEEP_SESSION", "SLEEP_HRV"}
                    and row["bucket_end"]
                    else row["bucket_start"],
                    tz_name,
                ),
                bucket_start=row["bucket_start"],
                bucket_end=row["bucket_end"],
                value1=row["value1"],
                value2=row["value2"],
                value3=row["value3"],
                value4=row["value4"],
                source=row["source"],
                quality=row["quality"],
                metadata=json.loads(row["metadata_json"])
                if row["metadata_json"]
                else None,
                updated_at=row["updated_at"],
            )
            for row in sample_rows
        ]
        return metrics, samples

    def latest_sample(self, user_id: str, sample_type: str) -> HealthSampleOut | None:
        """Return the most recent sample of ``sample_type`` for one user.

        This is a raw "latest by bucket_start" lookup without day filtering so
        proactive sleep detection can check whether the user is asleep right now
        based on the freshest ``SLEEP_STAGE`` bucket.
        """
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM health_samples "
                "WHERE user_id = ? AND metric_type = ? "
                "ORDER BY bucket_start DESC LIMIT 1",
                (user_id, sample_type),
            ).fetchone()
        if row is None:
            return None
        return HealthSampleOut(
            metric_type=row["metric_type"],
            day=row["day"],
            bucket_start=row["bucket_start"],
            bucket_end=row["bucket_end"],
            value1=row["value1"],
            value2=row["value2"],
            value3=row["value3"],
            value4=row["value4"],
            source=row["source"],
            quality=row["quality"],
            metadata=json.loads(row["metadata_json"])
            if row["metadata_json"]
            else None,
            updated_at=row["updated_at"],
        )

    def list_samples(
        self,
        user_id: str,
        sample_types: list[str] | tuple[str, ...] | set[str],
    ) -> list[HealthSampleOut]:
        """Return all stored samples for selected types in UTC time order.

        Sleep scoring deliberately reads by absolute timestamps rather than the
        stored local ``day`` so overnight sessions remain correct across
        midnight and timezone changes.
        """
        types = sorted(set(sample_types))
        if not types:
            return []
        placeholders = ",".join("?" for _ in types)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM health_samples WHERE user_id = ? "
                f"AND metric_type IN ({placeholders}) ORDER BY bucket_start",
                (user_id, *types),
            ).fetchall()
        return [
            HealthSampleOut(
                metric_type=row["metric_type"],
                day=row["day"],
                bucket_start=row["bucket_start"],
                bucket_end=row["bucket_end"],
                value1=row["value1"],
                value2=row["value2"],
                value3=row["value3"],
                value4=row["value4"],
                source=row["source"],
                quality=row["quality"],
                metadata=json.loads(row["metadata_json"])
                if row["metadata_json"]
                else None,
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def clear(self, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM health_metrics WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM health_samples WHERE user_id = ?", (user_id,))
