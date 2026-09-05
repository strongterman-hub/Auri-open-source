from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from app.health.sleep_scoring import ALGORITHM_VERSION
from app.schemas.health import (
    SleepCalibrationOut,
    SleepScoreDimension,
    SleepScoreMeta,
    SleepScoreOut,
)


class SleepScoreStore:
    """Versioned storage for Auri sleep health and recovery scores."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

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
                CREATE TABLE IF NOT EXISTS sleep_scores (
                    user_id TEXT NOT NULL,
                    sleep_day TEXT NOT NULL,
                    session_start TEXT NOT NULL,
                    session_end TEXT NOT NULL,
                    sleep_health_score INTEGER,
                    sleep_health_confidence INTEGER NOT NULL DEFAULT 0,
                    recovery_score INTEGER,
                    recovery_confidence INTEGER NOT NULL DEFAULT 0,
                    calibration_state TEXT NOT NULL,
                    calibration_day INTEGER,
                    valid_nights INTEGER NOT NULL DEFAULT 0,
                    algorithm_version TEXT NOT NULL,
                    vendor_score INTEGER,
                    score_status TEXT NOT NULL,
                    components_json TEXT NOT NULL,
                    input_summary_json TEXT NOT NULL,
                    input_hash TEXT NOT NULL,
                    computed_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, sleep_day, algorithm_version)
                );

                CREATE INDEX IF NOT EXISTS idx_sleep_scores_scope
                    ON sleep_scores(user_id, sleep_day, algorithm_version);

                CREATE TABLE IF NOT EXISTS sleep_baselines (
                    user_id TEXT NOT NULL,
                    baseline_version INTEGER NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    state TEXT NOT NULL,
                    calibration_start TEXT NOT NULL,
                    calibration_end TEXT NOT NULL,
                    valid_nights_json TEXT NOT NULL,
                    baseline_json TEXT NOT NULL,
                    device_fingerprint TEXT,
                    window_start TEXT NOT NULL,
                    window_end TEXT NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (user_id, baseline_version)
                );

                CREATE INDEX IF NOT EXISTS idx_sleep_baselines_latest
                    ON sleep_baselines(user_id, algorithm_version, baseline_version DESC);
                """
            )

    def replace_scores(
        self,
        user_id: str,
        rows: list[dict[str, Any]],
        algorithm_version: str = ALGORITHM_VERSION,
    ) -> int:
        with self._connect() as connection:
            existing = {
                row["sleep_day"]: (row["input_hash"], row["computed_at"])
                for row in connection.execute(
                    "SELECT sleep_day, input_hash, computed_at FROM sleep_scores "
                    "WHERE user_id = ? AND algorithm_version = ?",
                    (user_id, algorithm_version),
                ).fetchall()
            }
            connection.execute(
                "DELETE FROM sleep_scores WHERE user_id = ? AND algorithm_version = ?",
                (user_id, algorithm_version),
            )
            if rows:
                connection.executemany(
                    """
                    INSERT INTO sleep_scores (
                        user_id, sleep_day, session_start, session_end,
                        sleep_health_score, sleep_health_confidence,
                        recovery_score, recovery_confidence,
                        calibration_state, calibration_day, valid_nights,
                        algorithm_version, vendor_score, score_status,
                        components_json, input_summary_json, input_hash, computed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            user_id,
                            row["sleep_day"],
                            row["session_start"],
                            row["session_end"],
                            row["sleep_health"]["score"],
                            row["sleep_health"]["confidence"],
                            row["recovery"]["score"],
                            row["recovery"]["confidence"],
                            row["calibration"]["state"],
                            row["calibration"]["day"],
                            row["calibration"]["valid_nights"],
                            algorithm_version,
                            row.get("vendor_score"),
                            row["score_status"],
                            json.dumps(
                                {
                                    "sleep_health": row["sleep_health"],
                                    "recovery": row["recovery"],
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            json.dumps(
                                row["input_summary"],
                                ensure_ascii=False,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                            row["input_hash"],
                            existing.get(row["sleep_day"], (None, row["computed_at"]))[1]
                            if existing.get(row["sleep_day"], (None, None))[0]
                            == row["input_hash"]
                            else row["computed_at"],
                        )
                        for row in rows
                    ],
                )
        return len(rows)

    def list_scores(
        self,
        user_id: str,
        from_day: str,
        to_day: str,
        algorithm_version: str = ALGORITHM_VERSION,
    ) -> list[SleepScoreOut]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sleep_scores WHERE user_id = ? AND algorithm_version = ? "
                "AND sleep_day >= ? AND sleep_day <= ? ORDER BY sleep_day",
                (user_id, algorithm_version, from_day, to_day),
            ).fetchall()
        result: list[SleepScoreOut] = []
        for row in rows:
            components = json.loads(row["components_json"])
            result.append(
                SleepScoreOut(
                    sleep_day=row["sleep_day"],
                    session_start=row["session_start"],
                    session_end=row["session_end"],
                    sleep_health=SleepScoreDimension(**components["sleep_health"]),
                    recovery=SleepScoreDimension(**components["recovery"]),
                    calibration=SleepCalibrationOut(
                        state=row["calibration_state"],
                        day=row["calibration_day"],
                        total_days=14,
                        valid_nights=row["valid_nights"],
                    ),
                    meta=SleepScoreMeta(
                        algorithm_version=row["algorithm_version"],
                        vendor_score=row["vendor_score"],
                        computed_at=row["computed_at"],
                    ),
                )
            )
        return result

    def has_scores(
        self,
        user_id: str,
        algorithm_version: str = ALGORITHM_VERSION,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sleep_scores WHERE user_id = ? AND algorithm_version = ? LIMIT 1",
                (user_id, algorithm_version),
            ).fetchone()
        return row is not None

    def save_baseline(
        self,
        user_id: str,
        *,
        state: str,
        calibration_start: str,
        calibration_end: str,
        valid_nights: dict[str, int],
        baseline: dict[str, Any],
        window_start: str,
        window_end: str,
        updated_at: int,
        device_fingerprint: str | None = None,
        algorithm_version: str = ALGORITHM_VERSION,
    ) -> None:
        with self._connect() as connection:
            valid_nights_json = json.dumps(valid_nights, sort_keys=True)
            baseline_json = json.dumps(baseline, sort_keys=True, separators=(",", ":"))
            previous = connection.execute(
                "SELECT * FROM sleep_baselines WHERE user_id = ? AND algorithm_version = ? "
                "ORDER BY baseline_version DESC LIMIT 1",
                (user_id, algorithm_version),
            ).fetchone()
            if previous is not None and all(
                (
                    previous["state"] == state,
                    previous["calibration_start"] == calibration_start,
                    previous["calibration_end"] == calibration_end,
                    previous["valid_nights_json"] == valid_nights_json,
                    previous["baseline_json"] == baseline_json,
                    previous["device_fingerprint"] == device_fingerprint,
                    previous["window_start"] == window_start,
                    previous["window_end"] == window_end,
                )
            ):
                return
            latest = connection.execute(
                "SELECT COALESCE(MAX(baseline_version), 0) AS version "
                "FROM sleep_baselines WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            version = int(latest["version"]) + 1
            connection.execute(
                """
                INSERT INTO sleep_baselines (
                    user_id, baseline_version, algorithm_version, state,
                    calibration_start, calibration_end, valid_nights_json,
                    baseline_json, device_fingerprint, window_start, window_end,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    version,
                    algorithm_version,
                    state,
                    calibration_start,
                    calibration_end,
                    valid_nights_json,
                    baseline_json,
                    device_fingerprint,
                    window_start,
                    window_end,
                    updated_at,
                ),
            )
            # Keep a small audit trail rather than growing forever on every sync.
            connection.execute(
                "DELETE FROM sleep_baselines WHERE user_id = ? AND baseline_version NOT IN "
                "(SELECT baseline_version FROM sleep_baselines WHERE user_id = ? "
                "ORDER BY baseline_version DESC LIMIT 10)",
                (user_id, user_id),
            )

    def clear(self, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM sleep_scores WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM sleep_baselines WHERE user_id = ?", (user_id,))
