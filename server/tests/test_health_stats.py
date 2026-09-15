from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.agent.tools import HealthStatsTool
from app.health.stats import compare, summary, trend
from app.health.store import HealthStore
from app.schemas.health import HealthMetricIn, HealthMetricOut, HealthSampleOut
from app.services.health_service import HealthService


def test_summary_and_trend() -> None:
    metrics = [
        HealthMetricOut(metric_type="STEPS", day="2026-08-20", value1=1000),
        HealthMetricOut(metric_type="STEPS", day="2026-08-21", value1=2000),
        HealthMetricOut(metric_type="STEPS", day="2026-08-22", value1=3000),
        HealthMetricOut(metric_type="SLEEP", day="2026-08-22", value1=420, value3=85),
    ]

    result = summary(metrics, "2026-08-20", "2026-08-22")
    assert result["metrics"]["STEPS"]["value1"]["sum"] == 6000
    assert result["metrics"]["STEPS"]["value1"]["avg"] == 2000
    assert result["highlights"]["steps_total"] == 6000
    assert result["highlights"]["sleep_score_avg"] == 85

    series = trend(metrics, "STEPS", "2026-08-20", "2026-08-22")
    assert len(series["series"]) == 3
    assert series["stats"]["avg"] == 2000


def test_compare_windows() -> None:
    metrics = [
        HealthMetricOut(metric_type="STEPS", day="2026-08-10", value1=1000),
        HealthMetricOut(metric_type="STEPS", day="2026-08-11", value1=2000),
        HealthMetricOut(metric_type="STEPS", day="2026-08-20", value1=3000),
    ]
    result = compare(
        metrics,
        "STEPS",
        "2026-08-10",
        "2026-08-11",
        "2026-08-20",
        "2026-08-20",
    )
    assert result["a"]["stats"]["avg"] == 1500
    assert result["b"]["stats"]["avg"] == 3000
    assert result["delta_avg"] == 1500
    assert result["delta_pct"] == 100.0


def test_health_stats_tool_summary(tmp_dir: Path) -> None:
    store = HealthStore(tmp_dir / "health")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    store.sync(
        user_id="u1",
        from_day=today,
        to_day=today,
        metric_types=["STEPS"],
        metrics=[HealthMetricIn(metric_type="STEPS", day=today, value1=3000)],
        samples=[],
    )
    tool = HealthStatsTool(HealthService(store), "u1", "Asia/Shanghai")

    payload = json.loads(asyncio.run(tool.execute(operation="summary", days=7)))

    assert payload["highlights"]["steps_total"] == 3000
    assert payload["metrics"]["STEPS"]["latest_day"] == today

class FakeSleepHealthService:
    def __init__(self) -> None:
        self.metric = HealthMetricOut(
            metric_type="SLEEP", day="2026-09-14", value1=575, updated_at=1
        )
        self.sample = HealthSampleOut(
            metric_type="SLEEP_SESSION",
            day="2026-09-14",
            bucket_start="2026-09-14T06:00:00+00:00",
            bucket_end="2026-09-14T08:38:00+00:00",
            value1=158,
            value4="nap",
        )
        self.score = SimpleNamespace(
            sleep_day="2026-09-14",
            session_start="2026-09-13T17:41:00Z",
            session_end="2026-09-14T00:42:00Z",
        )

    def get_metrics(self, *_args, **_kwargs):
        return [self.metric], [self.sample]

    def get_sleep_scores(self, *_args, **_kwargs):
        return [self.score]


def test_health_stats_trend_includes_sleep_evidence() -> None:
    tool = HealthStatsTool(FakeSleepHealthService(), "u1", "Asia/Shanghai")
    payload = json.loads(
        asyncio.run(
            tool.execute(
                operation="trend",
                metric_type="SLEEP",
                from_day="2026-09-14",
                to_day="2026-09-14",
            )
        )
    )
    evidence = payload["sleep_evidence"][0]
    assert evidence["daily_total_sleep_minutes"] == 575
    assert evidence["main_sleep_episode"]["interval_minutes"] == 421
    assert evidence["nap_minutes"] == 158


def test_health_stats_compare_includes_sleep_evidence() -> None:
    tool = HealthStatsTool(FakeSleepHealthService(), "u1", "Asia/Shanghai")
    payload = json.loads(
        asyncio.run(
            tool.execute(
                operation="compare",
                metric_type="SLEEP",
                from_day="2026-09-13",
                to_day="2026-09-13",
                compare_from_day="2026-09-14",
                compare_to_day="2026-09-14",
            )
        )
    )
    assert payload["sleep_evidence"][0]["main_sleep_episode"]["interval_minutes"] == 421
