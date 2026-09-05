from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.agent.tools import HealthStatsTool
from app.health.store import HealthStore
from app.schemas.health import HealthMetricIn, HealthSampleIn
from app.services.health_service import HealthService
from app.services.timezone_store import TimezoneResolver, UserTimezoneStore, normalize_timezone


def test_normalize_timezone_falls_back() -> None:
    assert normalize_timezone("America/New_York") == "America/New_York"
    assert normalize_timezone("Not/AZone") == "Asia/Shanghai"


def test_user_timezone_store_roundtrip(tmp_dir: Path) -> None:
    store = UserTimezoneStore(tmp_dir / "user_timezones.json")
    assert store.set("u1", "America/New_York") == "America/New_York"
    assert store.get("u1") == "America/New_York"
    resolver = TimezoneResolver(store, default_timezone="Asia/Shanghai")
    assert resolver.get("u1") == "America/New_York"
    assert resolver.get("unknown") == "Asia/Shanghai"


def test_sample_day_recomputed_per_timezone(tmp_dir: Path) -> None:
    store = HealthStore(tmp_dir / "health")
    store.sync(
        user_id="u1",
        from_day="2026-08-19",
        to_day="2026-08-19",
        metric_types=[],
        metrics=[],
        samples=[
            HealthSampleIn(
                metric_type="HEART_RATE",
                day="2026-08-19",
                bucket_start="2026-08-19T16:30:00Z",
                bucket_end="2026-08-19T16:45:00Z",
                value1=70.0,
            )
        ],
        sample_types=["HEART_RATE"],
        tz="UTC",
    )

    _, utc_samples = store.get_metrics("u1", "2026-08-19", "2026-08-19", tz="UTC")
    assert [sample.day for sample in utc_samples] == ["2026-08-19"]

    _, cn_samples = store.get_metrics(
        "u1", "2026-08-20", "2026-08-20", tz="Asia/Shanghai"
    )
    assert [sample.day for sample in cn_samples] == ["2026-08-20"]


def test_sleep_session_is_filtered_by_local_wake_day(tmp_dir: Path) -> None:
    store = HealthStore(tmp_dir / "health")
    store.sync(
        user_id="u1",
        from_day="2026-08-20",
        to_day="2026-08-20",
        metric_types=[],
        metrics=[],
        samples=[
            HealthSampleIn(
                metric_type="SLEEP_SESSION",
                day="2026-08-20",
                bucket_start="2026-08-19T15:30:00Z",
                bucket_end="2026-08-20T00:00:00Z",
                value1=510.0,
                value2=480.0,
                value4="main",
            )
        ],
        sample_types=["SLEEP_SESSION"],
        tz="Asia/Shanghai",
    )

    _, samples = store.get_metrics(
        "u1", "2026-08-20", "2026-08-20", tz="Asia/Shanghai"
    )

    assert len(samples) == 1
    assert samples[0].day == "2026-08-20"
    assert samples[0].bucket_start == "2026-08-19T15:30:00Z"


def test_metric_day_requeried_in_another_timezone(tmp_dir: Path) -> None:
    store = HealthStore(tmp_dir / "health")
    store.sync(
        user_id="u1",
        from_day="2026-08-19",
        to_day="2026-08-19",
        metric_types=["STEPS"],
        metrics=[HealthMetricIn(metric_type="STEPS", day="2026-08-19", value1=1000.0)],
        samples=[],
        tz="Asia/Shanghai",
    )

    cn_metrics, _ = store.get_metrics("u1", "2026-08-19", "2026-08-19", tz="Asia/Shanghai")
    assert [metric.day for metric in cn_metrics] == ["2026-08-19"]

    utc_metrics, _ = store.get_metrics("u1", "2026-08-18", "2026-08-18", tz="UTC")
    assert [metric.day for metric in utc_metrics] == ["2026-08-18"]


def test_backfill_day_start_for_legacy_rows(tmp_dir: Path) -> None:
    store = HealthStore(tmp_dir / "health")
    store.sync(
        user_id="u1",
        from_day="2026-08-19",
        to_day="2026-08-19",
        metric_types=["STEPS"],
        metrics=[HealthMetricIn(metric_type="STEPS", day="2026-08-19", value1=1000.0)],
        samples=[],
        tz="Asia/Shanghai",
    )
    with store._connect() as connection:
        connection.execute("UPDATE health_metrics SET day_start = NULL")

    reopened = HealthStore(tmp_dir / "health")
    metrics, _ = reopened.get_metrics("u1", "2026-08-19", "2026-08-19", tz="Asia/Shanghai")
    assert [metric.day for metric in metrics] == ["2026-08-19"]


def test_health_store_adds_metric_provenance_columns_to_legacy_db(tmp_dir: Path) -> None:
    root = tmp_dir / "health"
    root.mkdir(parents=True)
    with sqlite3.connect(root / "health.db") as connection:
        connection.execute(
            """
            CREATE TABLE health_metrics (
                user_id TEXT NOT NULL,
                metric_type TEXT NOT NULL,
                day TEXT NOT NULL,
                day_start TEXT,
                value1 REAL,
                value2 REAL,
                value3 REAL,
                updated_at INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, metric_type, day)
            )
            """
        )
        connection.execute(
            "INSERT INTO health_metrics "
            "(user_id, metric_type, day, day_start, value1, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "u1",
                "STEPS",
                "2026-08-19",
                "2026-08-18T16:00:00Z",
                1000.0,
                123_000,
            ),
        )

    store = HealthStore(root)
    with store._connect() as connection:
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(health_metrics)")
        }
    assert {"source", "source_updated_at", "resolution_policy"} <= columns

    metrics, _ = store.get_metrics("u1", "2026-08-19", "2026-08-19")
    assert len(metrics) == 1
    assert metrics[0].source is None
    assert metrics[0].source_updated_at == 0
    assert metrics[0].resolution_policy is None


def test_health_stats_tool_uses_resolver_timezone(tmp_dir: Path) -> None:
    store = HealthStore(tmp_dir / "health")
    tz_name = "America/New_York"
    today = datetime.now(ZoneInfo(tz_name)).date().isoformat()
    store.sync(
        user_id="u1",
        from_day=today,
        to_day=today,
        metric_types=["STEPS"],
        metrics=[HealthMetricIn(metric_type="STEPS", day=today, value1=3000.0)],
        samples=[],
        tz=tz_name,
    )
    tool = HealthStatsTool(
        HealthService(store),
        "u1",
        timezone_resolver=lambda user_id: tz_name,
    )

    payload = json.loads(asyncio.run(tool.execute(operation="summary", days=7)))

    assert payload["highlights"]["steps_total"] == 3000
    assert payload["metrics"]["STEPS"]["latest_day"] == today
