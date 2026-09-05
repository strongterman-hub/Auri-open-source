from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.auth.store import AuthStore
from app.config import Settings
from app.health.backfill_sleep_scores import backfill_all
from app.health.sleep_score_service import SleepScoreService
from app.health.sleep_score_store import SleepScoreStore
from app.health.store import HealthStore
from app.schemas.health import HealthSampleIn


def _iso(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _sleep_samples() -> list[HealthSampleIn]:
    samples: list[HealthSampleIn] = []
    for index in range(15):
        wake_day = datetime(2026, 8, 2, 7, 0, tzinfo=UTC) + timedelta(days=index)
        start = wake_day - timedelta(hours=8)
        samples.append(
            HealthSampleIn(
                metric_type="SLEEP_SESSION",
                day=wake_day.date().isoformat(),
                bucket_start=_iso(start),
                bucket_end=_iso(wake_day),
                value1=480,
                value2=450,
                value3=88,
                value4="main",
                source="xiaomi",
            )
        )
        for bucket in range(32):
            bucket_start = start + timedelta(minutes=15 * bucket)
            samples.append(
                HealthSampleIn(
                    metric_type="HEART_RATE",
                    day=bucket_start.date().isoformat(),
                    bucket_start=_iso(bucket_start),
                    bucket_end=_iso(bucket_start + timedelta(minutes=15)),
                    value1=64 - min(bucket, 14) * 0.3,
                )
            )
        rhr_at = start + timedelta(hours=3)
        samples.append(
            HealthSampleIn(
                metric_type="RESTING_HEART_RATE",
                day=rhr_at.date().isoformat(),
                bucket_start=_iso(rhr_at),
                bucket_end=_iso(rhr_at + timedelta(seconds=1)),
                value1=58,
                source="xiaomi",
            )
        )

    # A longer daytime nap must never replace the explicit main sleep.
    nap_start = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
    samples.append(
        HealthSampleIn(
            metric_type="SLEEP_SESSION",
            day="2026-08-16",
            bucket_start=_iso(nap_start),
            bucket_end=_iso(nap_start + timedelta(hours=10)),
            value1=600,
            value2=590,
            value4="nap",
        )
    )
    return samples


def test_score_service_backfills_calibration_and_preserves_rhr_time(tmp_dir: Path) -> None:
    health_store = HealthStore(tmp_dir / "health")
    samples = _sleep_samples()
    health_store.sync(
        user_id="u1",
        from_day="2026-08-01",
        to_day="2026-08-31",
        metric_types=[],
        metrics=[],
        samples=samples,
        sample_types=["SLEEP_SESSION", "HEART_RATE", "RESTING_HEART_RATE"],
        tz="UTC",
    )
    score_store = SleepScoreStore(health_store.db_path)
    service = SleepScoreService(health_store, score_store)

    assert service.recompute_all("u1", "UTC") == 15
    scores = service.get_scores("u1", "2026-08-01", "2026-08-31")
    assert len(scores) == 15
    assert scores[0].sleep_day == "2026-08-02"
    assert scores[0].calibration.state == "calibrating"
    assert scores[-1].calibration.state == "calibrated"
    assert scores[-1].calibration.valid_nights == 15
    assert scores[-1].recovery.status == "partial"
    assert scores[-1].recovery.confidence <= 60
    heart_component = scores[-1].recovery.components["night_heart_rate"]
    assert heart_component.value["device_rhr_bpm"] == 58.0
    assert heart_component.value["device_rhr_at"] is not None
    assert scores[-1].meta.vendor_score == 88

    computed_at = scores[-1].meta.computed_at
    assert service.recompute_all("u1", "UTC") == 15
    assert service.get_scores("u1", "2026-08-16", "2026-08-16")[0].meta.computed_at == computed_at


def test_sleep_sample_metadata_roundtrip(tmp_dir: Path) -> None:
    store = HealthStore(tmp_dir / "health")
    sample = HealthSampleIn(
        metric_type="SLEEP_HRV",
        day="2026-08-02",
        bucket_start="2026-08-01T23:00:00Z",
        bucket_end="2026-08-02T07:00:00Z",
        value1=52,
        source="xiaomi",
        quality=0.9,
        metadata={"method": "device_sleep_rmssd"},
    )
    store.sync(
        "u1",
        "2026-08-01",
        "2026-08-02",
        [],
        [],
        [sample],
        sample_types=["SLEEP_HRV"],
        tz="UTC",
    )
    restored = store.list_samples("u1", ["SLEEP_HRV"])[0]
    assert restored.source == "xiaomi"
    assert restored.quality == 0.9
    assert restored.metadata == {"method": "device_sleep_rmssd"}


def test_score_store_clear_removes_scores_and_baselines(tmp_dir: Path) -> None:
    health_store = HealthStore(tmp_dir / "health")
    score_store = SleepScoreStore(health_store.db_path)
    score_store.clear("u1")
    assert score_store.list_scores("u1", "1970-01-01", "9999-12-31") == []


def test_backfill_all_recomputes_registered_users(tmp_dir: Path) -> None:
    AuthStore(tmp_dir / "auth").create_user("sleep@example.com", "test1234")
    health_store = HealthStore(tmp_dir / "health")
    samples = _sleep_samples()
    health_store.sync(
        user_id="sleep@example.com",
        from_day="2026-08-01",
        to_day="2026-08-31",
        metric_types=[],
        metrics=[],
        samples=samples,
        sample_types=["SLEEP_SESSION", "HEART_RATE", "RESTING_HEART_RATE"],
        tz="UTC",
    )

    result = backfill_all(Settings(data_dir=tmp_dir, default_timezone="UTC"))

    assert result["errors"] == []
    assert result["user_count"] == 1
    assert result["total_scores"] == 15
    assert result["users"][0]["recomputed"] == 15
