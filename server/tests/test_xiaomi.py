from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet

from app.health.store import HealthStore
from app.schemas.health import HealthMetricIn, HealthSampleIn
from app.integrations.xiaomi.credential_store import CredentialStore
from app.integrations.xiaomi.models import (
    AbnormalHeartBeatEvent,
    BodyMeasurement,
    DailyActivity,
    HeartRateSample,
    SleepSession,
    SleepStage,
    SpO2Sample,
    StressSample,
    Workout,
)
from app.integrations.xiaomi.service import (
    _bucket_heart_rate,
    _bucket_sleep_stages,
    XiaomiService,
)

TZ = timezone(timedelta(hours=8))


class FakeClient:
    def get_available_data_types(self) -> list[str]:
        return ["daily_activity", "heart_rate", "body_measurements", "sleep"]

    async def iter_daily_activity(self, start: str, end: str):
        yield DailyActivity(
            date="2026-08-19",
            steps=1000,
            distance_m=500.0,
            active_kcal=30.0,
            steps_source="xiaomi_aggregate",
            distance_source="xiaomi_aggregate",
            calories_source="xiaomi_device",
            steps_source_updated_at=123_000,
            distance_source_updated_at=123_000,
            calories_source_updated_at=124_000,
        )

    async def iter_body_measurements(self, start: str, end: str):
        yield BodyMeasurement(
            timestamp=datetime(2026, 8, 19, 8, 0, 0, tzinfo=TZ), weight_kg=60.5
        )

    async def iter_heart_rate(self, start: str, end: str):
        yield HeartRateSample(timestamp=datetime(2026, 8, 19, 2, 0, 0, tzinfo=TZ), bpm=60)
        yield HeartRateSample(timestamp=datetime(2026, 8, 19, 2, 10, 0, tzinfo=TZ), bpm=80)

    async def iter_sleep_sessions(self, start: str, end: str):
        yield SleepSession(
            start_at=datetime(2026, 8, 18, 23, 0, 0, tzinfo=TZ),
            end_at=datetime(2026, 8, 19, 6, 30, 0, tzinfo=TZ),
            duration_minutes=450,
            time_asleep_minutes=420,
            time_awake_minutes=30,
            sleep_score=85,
            stages=[
                SleepStage(
                    stage="deep",
                    start_time=datetime(2026, 8, 18, 23, 0, 0, tzinfo=TZ),
                    end_time=datetime(2026, 8, 19, 0, 0, 0, tzinfo=TZ),
                )
            ],
        )

    async def iter_spo2(self, start: str, end: str):
        return
        yield

    async def iter_stress(self, start: str, end: str):
        return
        yield

    async def iter_workouts(self, start: str, end: str):
        return
        yield

    async def iter_abnormal_heart_beat(self, start: str, end: str):
        return
        yield


class ExtendedFakeClient(FakeClient):
    async def iter_heart_rate(self, start: str, end: str):
        yield HeartRateSample(
            timestamp=datetime(2026, 8, 19, 2, 0, 0, tzinfo=TZ), bpm=62, sample_type="resting"
        )
        yield HeartRateSample(
            timestamp=datetime(2026, 8, 19, 2, 10, 0, tzinfo=TZ), bpm=80, sample_type="passive"
        )

    async def iter_body_measurements(self, start: str, end: str):
        yield BodyMeasurement(
            timestamp=datetime(2026, 8, 19, 8, 0, 0, tzinfo=TZ),
            weight_kg=60.5,
            bmi=21.0,
            body_fat_pct=22.0,
            muscle_mass_kg=40.0,
            water_pct=55.0,
            bone_mass_kg=2.5,
            visceral_fat_score=8,
            basal_metabolism_kcal=1400,
            metabolic_age=28,
        )

    async def iter_spo2(self, start: str, end: str):
        yield SpO2Sample(timestamp=datetime(2026, 8, 19, 2, 0, 0, tzinfo=TZ), spo2_pct=97)
        yield SpO2Sample(timestamp=datetime(2026, 8, 19, 2, 10, 0, tzinfo=TZ), spo2_pct=98)

    async def iter_stress(self, start: str, end: str):
        yield StressSample(
            timestamp=datetime(2026, 8, 19, 2, 0, 0, tzinfo=TZ), stress_score=35, level="medium"
        )

    async def iter_workouts(self, start: str, end: str):
        yield Workout(
            start_at=datetime(2026, 8, 19, 7, 0, 0, tzinfo=TZ),
            end_at=datetime(2026, 8, 19, 7, 30, 0, tzinfo=TZ),
            duration_minutes=30,
            activity_type="running",
            calories_kcal=200.0,
            avg_heart_rate_bpm=140,
        )

    async def iter_abnormal_heart_beat(self, start: str, end: str):
        yield AbnormalHeartBeatEvent(
            start_at=datetime(2026, 8, 19, 3, 0, 0, tzinfo=TZ),
            end_at=datetime(2026, 8, 19, 3, 0, 12, tzinfo=TZ),
            duration_seconds=12,
        )


def test_bucket_heart_rate() -> None:
    samples = [
        HeartRateSample(timestamp=datetime(2026, 8, 19, 2, 0, 0, tzinfo=TZ), bpm=60),
        HeartRateSample(timestamp=datetime(2026, 8, 19, 2, 10, 0, tzinfo=TZ), bpm=80),
    ]
    buckets = _bucket_heart_rate(samples, 15, 0)
    assert len(buckets) == 1
    assert buckets[0].value1 == 70.0
    assert buckets[0].value2 == 60.0
    assert buckets[0].value3 == 80.0


def test_bucket_sleep_stages_splits_midnight_day() -> None:
    stage = SleepStage(
        stage="deep",
        start_time=datetime(2026, 8, 18, 23, 55, 0, tzinfo=TZ),
        end_time=datetime(2026, 8, 19, 0, 10, 0, tzinfo=TZ),
    )
    buckets = _bucket_sleep_stages([stage], 5, 0)
    assert len(buckets) == 3
    assert buckets[0].day == "2026-08-18"
    assert buckets[-1].day == "2026-08-19"
    assert all(bucket.value1 == 1.0 for bucket in buckets)


def test_credential_store_roundtrip(tmp_dir: Path) -> None:
    key = Fernet.generate_key().decode()
    store = CredentialStore(tmp_dir, key)
    store.save("u1", "mi-1", "pass-1")
    credentials = store.load("u1")
    assert credentials is not None
    assert credentials.mi_user_id == "mi-1"
    assert credentials.pass_token == "pass-1"
    store.delete("u1")
    assert store.load("u1") is None


def test_pull_and_map(tmp_dir: Path) -> None:
    service = XiaomiService(
        HealthStore(tmp_dir / "health"),
        CredentialStore(tmp_dir / "credentials", Fernet.generate_key().decode()),
    )
    metrics, samples = asyncio.run(
        service._pull_and_map(FakeClient(), "2026-08-01", "2026-08-19", 123456)
    )
    metric_types = {metric.metric_type for metric in metrics}
    assert metric_types == {"STEPS", "DISTANCE", "CALORIES", "HEART_RATE", "SLEEP", "WEIGHT"}
    metric_by_type = {metric.metric_type: metric for metric in metrics}
    assert metric_by_type["STEPS"].source == "xiaomi_aggregate"
    assert metric_by_type["STEPS"].source_updated_at == 123_000
    assert metric_by_type["STEPS"].resolution_policy == "xiaomi_aggregate_preferred"
    assert metric_by_type["CALORIES"].source == "xiaomi_device"
    assert metric_by_type["CALORIES"].resolution_policy == "xiaomi_device_preferred"
    sample_types = {sample.metric_type for sample in samples}
    assert sample_types == {"HEART_RATE", "SLEEP_STAGE", "SLEEP_SESSION"}


def test_pull_and_map_extended_types(tmp_dir: Path) -> None:
    service = XiaomiService(
        HealthStore(tmp_dir / "health"),
        CredentialStore(tmp_dir / "credentials", Fernet.generate_key().decode()),
    )
    metrics, samples = asyncio.run(
        service._pull_and_map(ExtendedFakeClient(), "2026-08-01", "2026-08-19", 123456)
    )
    metric_types = {metric.metric_type for metric in metrics}
    assert {
        "RESTING_HEART_RATE",
        "SPO2",
        "STRESS",
        "BODY_FAT",
        "BMI",
        "MUSCLE_MASS",
        "BODY_WATER",
        "BONE_MASS",
        "VISCERAL_FAT",
        "BMR",
    } <= metric_types
    sample_types = {sample.metric_type for sample in samples}
    assert {
        "RESTING_HEART_RATE",
        "SPO2",
        "STRESS",
        "WORKOUT",
        "ABNORMAL_HEART_BEAT",
    } <= sample_types
    resting = next(sample for sample in samples if sample.metric_type == "RESTING_HEART_RATE")
    assert resting.value1 == 62.0
    assert resting.bucket_start == "2026-08-18T18:00:00Z"


def test_sleep_session_uses_wake_day_and_explicit_nap_flag(tmp_dir: Path) -> None:
    service = XiaomiService(
        HealthStore(tmp_dir / "health"),
        CredentialStore(tmp_dir / "credentials", Fernet.generate_key().decode()),
    )
    metrics, samples = asyncio.run(
        service._pull_and_map(FakeClient(), "2026-08-01", "2026-08-19", 123456)
    )
    sleep_metric = next(metric for metric in metrics if metric.metric_type == "SLEEP")
    session = next(sample for sample in samples if sample.metric_type == "SLEEP_SESSION")
    assert sleep_metric.day == "2026-08-19"
    assert session.day == "2026-08-19"
    assert session.value4 == "main"
    assert session.value3 == 85.0


def test_xiaomi_sleep_hrv_parser_accepts_only_plausible_explicit_values() -> None:
    from app.integrations.xiaomi.client import XiaomiCloudClient

    client = XiaomiCloudClient()
    assert client._sleep_hrv_rmssd({"avg_sleep_hrv": 52}) == (52.0, "avg_sleep_hrv")
    assert client._sleep_hrv_rmssd({"heart_rate": 52}) == (None, None)
    assert client._sleep_hrv_rmssd({"avg_sleep_hrv": 900}) == (None, None)


def test_health_store_clears_explicit_sample_types(tmp_dir: Path) -> None:
    store = HealthStore(tmp_dir)
    store.sync(
        user_id="u1",
        from_day="2026-08-01",
        to_day="2026-08-31",
        metric_types=["STEPS"],
        metrics=[HealthMetricIn(metric_type="STEPS", day="2026-08-10", value1=1.0)],
        samples=[
            HealthSampleIn(
                metric_type="HEART_RATE",
                day="2026-08-10",
                bucket_start="2026-08-10T00:00:00Z",
                bucket_end="2026-08-10T00:15:00Z",
                value1=70.0,
            )
        ],
    )
    store.sync(
        user_id="u1",
        from_day="2026-08-01",
        to_day="2026-08-31",
        metric_types=["STEPS"],
        metrics=[],
        samples=[],
        sample_types=["HEART_RATE"],
    )
    metrics, samples = store.get_metrics("u1", "2026-08-01", "2026-08-31")
    assert metrics == []
    assert samples == []


def test_health_store_roundtrips_metric_source_provenance(tmp_dir: Path) -> None:
    store = HealthStore(tmp_dir)
    store.sync(
        user_id="u1",
        from_day="2026-08-19",
        to_day="2026-08-19",
        metric_types=["STEPS"],
        metrics=[
            HealthMetricIn(
                metric_type="STEPS",
                day="2026-08-19",
                value1=8334.0,
                source="xiaomi_aggregate",
                source_updated_at=123_000,
                resolution_policy="xiaomi_aggregate_preferred",
                updated_at=456_000,
            )
        ],
        samples=[],
    )

    metrics, _ = store.get_metrics("u1", "2026-08-19", "2026-08-19")
    assert len(metrics) == 1
    assert metrics[0].source == "xiaomi_aggregate"
    assert metrics[0].source_updated_at == 123_000
    assert metrics[0].resolution_policy == "xiaomi_aggregate_preferred"
