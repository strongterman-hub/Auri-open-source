from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from statistics import mean
from zoneinfo import ZoneInfo

from app.core.errors import ValidationError
from app.health.store import HealthStore
from app.health.sleep_score_service import SleepScoreService
from app.health.types import ALL_METRIC_TYPES
from app.integrations.xiaomi.client import XiaomiCloudClient
from app.integrations.xiaomi.credential_store import CredentialStore
from app.integrations.xiaomi.models import (
    BodyMeasurement,
    HeartRateSample,
    SleepSession,
    SleepStage,
)
from app.schemas.health import HealthMetricIn, HealthSampleIn
from app.services.observation_service import ObservationService
from app.services.timezone_store import TimezoneResolver


logger = logging.getLogger("auri.xiaomi")

# Sample types produced by the Xiaomi cloud sync. STEPS / CALORIES intraday
# samples come from the legacy Health Connect path, so the Xiaomi sync must not
# clear them.
XIAOMI_SAMPLE_TYPES = [
    "HEART_RATE",
    "RESTING_HEART_RATE",
    "SLEEP_STAGE",
    "SLEEP_SESSION",
    "SLEEP_HRV",
    "SPO2",
    "STRESS",
    "WORKOUT",
    "ABNORMAL_HEART_BEAT",
]

# The Android SleepStageChart renders sleep stages with these numeric values:
# 1=deep, 2=light, 3=rem, 4=awake. Keep server output aligned with that contract.
SLEEP_STAGE_VALUE = {"deep": 1.0, "light": 2.0, "rem": 3.0, "awake": 4.0}


def _epoch_millis() -> int:
    return int(time.time() * 1000)


def _day(dt: datetime, tz: timezone | ZoneInfo) -> str:
    return dt.astimezone(tz).date().isoformat()


def _iso_utc(dt: datetime) -> str:
    return (
        dt.astimezone(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _floor_epoch_seconds(dt: datetime, bucket_seconds: int) -> int:
    epoch = int(dt.timestamp())
    return epoch - (epoch % bucket_seconds)


def _bucket_series(
    metric_type: str,
    points: list[tuple[datetime, float]],
    bucket_minutes: int,
    updated_at: int,
    tz: timezone | ZoneInfo = ZoneInfo("Asia/Shanghai"),
) -> list[HealthSampleIn]:
    """Bucket timestamped values into fixed-minute buckets (mean/min/max)."""
    grouped: dict[int, list[float]] = defaultdict(list)
    for ts, value in points:
        start_epoch = _floor_epoch_seconds(ts, bucket_minutes * 60)
        grouped[start_epoch].append(value)

    result: list[HealthSampleIn] = []
    for start_epoch in sorted(grouped):
        values = grouped[start_epoch]
        start = datetime.fromtimestamp(start_epoch, tz=UTC)
        end = start + timedelta(minutes=bucket_minutes)
        result.append(
            HealthSampleIn(
                metric_type=metric_type,
                day=_day(start, tz),
                bucket_start=_iso_utc(start),
                bucket_end=_iso_utc(end),
                value1=mean(values),
                value2=min(values),
                value3=max(values),
                updated_at=updated_at,
            )
        )
    return result


def _bucket_heart_rate(
    samples: list[HeartRateSample],
    bucket_minutes: int,
    updated_at: int,
    tz: timezone | ZoneInfo = ZoneInfo("Asia/Shanghai"),
) -> list[HealthSampleIn]:
    return _bucket_series(
        "HEART_RATE",
        [(sample.timestamp, float(sample.bpm)) for sample in samples],
        bucket_minutes,
        updated_at,
        tz,
    )


def _daily_stat_metrics(
    metric_type: str,
    day_values: dict[str, list[float]],
    updated_at: int,
) -> list[HealthMetricIn]:
    """Build one mean/min/max metric per day from per-day value lists."""
    return [
        HealthMetricIn(
            metric_type=metric_type,
            day=day,
            value1=mean(values),
            value2=min(values),
            value3=max(values),
            updated_at=updated_at,
        )
        for day, values in sorted(day_values.items())
    ]


def _bucket_sleep_stages(
    stages: list[SleepStage],
    bucket_minutes: int,
    updated_at: int,
    tz: timezone | ZoneInfo = ZoneInfo("Asia/Shanghai"),
) -> list[HealthSampleIn]:
    result: list[HealthSampleIn] = []
    for stage in stages:
        if stage.start_time is None or stage.end_time is None:
            continue
        value = SLEEP_STAGE_VALUE.get(stage.stage)
        if value is None:
            continue

        cursor = _floor_epoch_seconds(stage.start_time, 60)
        end_epoch = int(stage.end_time.timestamp())
        while cursor < end_epoch:
            bucket_start = cursor
            bucket_end = min(cursor + bucket_minutes * 60, end_epoch)
            bucket_start_dt = datetime.fromtimestamp(bucket_start, tz=UTC)
            result.append(
                HealthSampleIn(
                    metric_type="SLEEP_STAGE",
                    day=_day(bucket_start_dt, tz),
                    bucket_start=_iso_utc(bucket_start_dt),
                    bucket_end=_iso_utc(datetime.fromtimestamp(bucket_end, tz=UTC)),
                    value1=value,
                    updated_at=updated_at,
                )
            )
            cursor = bucket_end
    return result


def _compute_sleep_score(session: SleepSession) -> int:
    """Compute a 0-100 sleep score from one main-sleep session.

    Follows the weighted scoring pattern used by open wearable sleep score
    implementations: duration, efficiency, deep-sleep share and REM share each
    contribute to a bounded sub-score. Naps carry no stage data and therefore
    do not produce a score.
    """
    stage_minutes: dict[str, int] = defaultdict(int)
    for stage in session.stages:
        stage_minutes[stage.stage] += max(0, stage.minutes)

    deep = stage_minutes.get("deep", 0)
    rem = stage_minutes.get("rem", 0)
    if deep + rem == 0:
        return 0

    duration = max(session.duration_minutes, session.time_asleep_minutes, 1)
    asleep = max(session.time_asleep_minutes, 1)

    duration_score = min(100.0, duration / 420.0 * 100.0)
    efficiency = min(100.0, asleep / duration * 100.0)
    efficiency_score = min(100.0, efficiency / 90.0 * 100.0)
    deep_pct = deep / duration * 100.0
    rem_pct = rem / duration * 100.0
    deep_score = min(100.0, deep_pct / 20.0 * 100.0)
    rem_score = min(100.0, rem_pct / 20.0 * 100.0)

    score = (
        0.4 * duration_score
        + 0.2 * efficiency_score
        + 0.2 * deep_score
        + 0.2 * rem_score
    )
    return int(round(min(100.0, max(0.0, score))))


class XiaomiService:
    """Orchestrates Xiaomi cloud pull -> Auri HealthStore mapping."""

    def __init__(
        self,
        health_store: HealthStore,
        credential_store: CredentialStore,
        sync_timeout_seconds: int = 180,
        observation_service: ObservationService | None = None,
        timezone_resolver: TimezoneResolver | None = None,
        sleep_score_service: SleepScoreService | None = None,
    ) -> None:
        self.health_store = health_store
        self.credential_store = credential_store
        self.sync_timeout_seconds = sync_timeout_seconds
        self.observation_service = observation_service
        self.timezone_resolver = timezone_resolver
        self.sleep_score_service = sleep_score_service

    def _timezone(self, user_id: str) -> ZoneInfo:
        if self.timezone_resolver is not None:
            return self.timezone_resolver.zoneinfo(user_id)
        return ZoneInfo("Asia/Shanghai")

    async def sync(self, auri_user_id: str, lookback_days: int = 30) -> dict:
        credentials = self.credential_store.load(auri_user_id)
        if credentials is None:
            raise ValidationError(message="尚未连接小米健康云，请先扫码登录")

        client = XiaomiCloudClient(
            user_id=credentials.mi_user_id,
            pass_token=credentials.pass_token,
            region="cn",
        )
        try:
            ok = await client.connect()
            if not ok:
                raise ValidationError(message=f"小米健康云登录失败：{client.last_error}")

            tz = self._timezone(auri_user_id)
            to_day = datetime.now(tz).date()
            from_day = to_day - timedelta(days=lookback_days - 1)
            from_str = from_day.isoformat()
            to_str = to_day.isoformat()

            updated_at = _epoch_millis()
            try:
                async with asyncio.timeout(self.sync_timeout_seconds):
                    metrics, samples = await self._pull_and_map(
                        client, from_str, to_str, updated_at, tz
                    )
            except TimeoutError as exc:
                raise ValidationError(
                    message=f"小米健康云同步超时（{self.sync_timeout_seconds} 秒）"
                ) from exc

            metric_types = sorted(ALL_METRIC_TYPES)
            synced = self.health_store.sync(
                user_id=auri_user_id,
                from_day=from_str,
                to_day=to_str,
                metric_types=metric_types,
                metrics=metrics,
                samples=samples,
                sample_types=XIAOMI_SAMPLE_TYPES,
                tz=tz.key,
            )
            sleep_scores = 0
            if self.sleep_score_service is not None:
                try:
                    sleep_scores = self.sleep_score_service.recompute_all(
                        auri_user_id,
                        tz.key,
                    )
                except Exception:
                    # Scoring is derived shadow data. Keep the completed Xiaomi
                    # raw-data sync usable and surface the failure in logs.
                    logger.exception(
                        "sleep score recompute failed after Xiaomi sync for user %s",
                        auri_user_id,
                    )

            if self.observation_service is not None:
                self.observation_service.ingest_health(
                    user_id=auri_user_id,
                    agent_id="default",
                    from_day=from_str,
                    to_day=to_str,
                    metrics=metrics,
                    samples=samples,
                )

            available = client.get_available_data_types()
            self.credential_store.update_status(
                auri_user_id,
                last_sync_at=_epoch_millis(),
                available_data_types=available,
            )

            return {
                "synced": synced,
                "metrics": len(metrics),
                "samples": len(samples),
                "sleep_scores": sleep_scores,
                "from_day": from_str,
                "to_day": to_str,
                "available_data_types": available,
            }
        finally:
            await client.close()

    def status(self, auri_user_id: str) -> dict:
        credentials = self.credential_store.load(auri_user_id)
        if credentials is None:
            return {
                "bound": False,
                "last_sync_at": None,
                "available_data_types": [],
            }
        return {
            "bound": True,
            "last_sync_at": credentials.last_sync_at or None,
            "available_data_types": credentials.available_data_types,
        }

    def disconnect(self, auri_user_id: str) -> None:
        self.credential_store.delete(auri_user_id)

    async def _pull_and_map(
        self,
        client: XiaomiCloudClient,
        from_day: str,
        to_day: str,
        updated_at: int,
        tz: timezone | ZoneInfo = ZoneInfo("Asia/Shanghai"),
    ) -> tuple[list[HealthMetricIn], list[HealthSampleIn]]:
        metrics: list[HealthMetricIn] = []
        samples: list[HealthSampleIn] = []

        async for activity in client.iter_daily_activity(from_day, to_day):
            metrics.append(
                HealthMetricIn(
                    metric_type="STEPS",
                    day=activity.date,
                    value1=float(activity.steps),
                    source=activity.steps_source,
                    source_updated_at=activity.steps_source_updated_at,
                    resolution_policy="xiaomi_aggregate_preferred",
                    updated_at=updated_at,
                )
            )
            metrics.append(
                HealthMetricIn(
                    metric_type="DISTANCE",
                    day=activity.date,
                    value1=float(activity.distance_m),
                    source=activity.distance_source,
                    source_updated_at=activity.distance_source_updated_at,
                    resolution_policy="xiaomi_aggregate_preferred",
                    updated_at=updated_at,
                )
            )
            metrics.append(
                HealthMetricIn(
                    metric_type="CALORIES",
                    day=activity.date,
                    value1=float(activity.active_kcal),
                    source=activity.calories_source,
                    source_updated_at=activity.calories_source_updated_at,
                    resolution_policy="xiaomi_device_preferred",
                    updated_at=updated_at,
                )
            )

        # Body composition: keep the latest measurement per day.
        body_by_day: dict[str, BodyMeasurement] = {}
        async for measurement in client.iter_body_measurements(from_day, to_day):
            day = _day(measurement.timestamp, tz)
            existing = body_by_day.get(day)
            if existing is None or measurement.timestamp >= existing.timestamp:
                body_by_day[day] = measurement

        body_metric_fields: list[tuple[str, str]] = [
            ("WEIGHT", "weight_kg"),
            ("BODY_FAT", "body_fat_pct"),
            ("BMI", "bmi"),
            ("MUSCLE_MASS", "muscle_mass_kg"),
            ("BODY_WATER", "water_pct"),
            ("BONE_MASS", "bone_mass_kg"),
            ("VISCERAL_FAT", "visceral_fat_score"),
            ("BMR", "basal_metabolism_kcal"),
        ]
        for day, measurement in sorted(body_by_day.items()):
            for metric_type, field in body_metric_fields:
                value = getattr(measurement, field)
                if value is not None:
                    metrics.append(
                        HealthMetricIn(
                            metric_type=metric_type,
                            day=day,
                            value1=float(value),
                            updated_at=updated_at,
                        )
                    )

        # Heart rate: all samples -> HEART_RATE; resting subset -> RESTING_HEART_RATE.
        heart_rate_by_day: dict[str, list[HeartRateSample]] = defaultdict(list)
        resting_by_day: dict[str, list[HeartRateSample]] = defaultdict(list)
        async for sample in client.iter_heart_rate(from_day, to_day):
            day = _day(sample.timestamp, tz)
            heart_rate_by_day[day].append(sample)
            if sample.sample_type == "resting":
                resting_by_day[day].append(sample)

        for day, day_samples in heart_rate_by_day.items():
            values = [sample.bpm for sample in day_samples]
            metrics.append(
                HealthMetricIn(
                    metric_type="HEART_RATE",
                    day=day,
                    value1=mean(values),
                    value2=min(values),
                    value3=max(values),
                    updated_at=updated_at,
                )
            )
            samples.extend(_bucket_heart_rate(day_samples, 15, updated_at, tz))

        for day, day_samples in resting_by_day.items():
            values = [sample.bpm for sample in day_samples]
            metrics.append(
                HealthMetricIn(
                    metric_type="RESTING_HEART_RATE",
                    day=day,
                    value1=mean(values),
                    value2=min(values),
                    value3=max(values),
                    updated_at=updated_at,
                )
            )
            for sample in day_samples:
                samples.append(
                    HealthSampleIn(
                        metric_type="RESTING_HEART_RATE",
                        day=day,
                        bucket_start=_iso_utc(sample.timestamp),
                        bucket_end=_iso_utc(sample.timestamp + timedelta(seconds=1)),
                        value1=float(sample.bpm),
                        value4="resting",
                        source="xiaomi",
                        quality=1.0,
                        metadata={"sample_type": sample.sample_type},
                        updated_at=updated_at,
                    )
                )

        sleep_by_day: dict[str, list[SleepSession]] = defaultdict(list)
        async for session in client.iter_sleep_sessions(from_day, to_day):
            sleep_by_day[_day(session.end_at, tz)].append(session)

        for day, day_sessions in sorted(sleep_by_day.items()):
            total_duration = sum(s.duration_minutes for s in day_sessions)
            total_asleep = sum(s.time_asleep_minutes for s in day_sessions)
            main_sessions = [s for s in day_sessions if not s.is_nap]
            best_score = max(
                (s.sleep_score or _compute_sleep_score(s) for s in main_sessions),
                default=0,
            )
            metrics.append(
                HealthMetricIn(
                    metric_type="SLEEP",
                    day=day,
                    value1=float(total_duration),
                    value2=float(total_asleep),
                    value3=float(best_score),
                    updated_at=updated_at,
                )
            )
            for session in day_sessions:
                if session.stages:
                    samples.extend(_bucket_sleep_stages(session.stages, 5, updated_at, tz))
                samples.append(
                    HealthSampleIn(
                        metric_type="SLEEP_SESSION",
                        day=day,
                        bucket_start=_iso_utc(session.start_at),
                        bucket_end=_iso_utc(session.end_at),
                        value1=float(session.duration_minutes),
                        value2=float(session.time_asleep_minutes),
                        value3=float(session.sleep_score)
                        if session.sleep_score is not None
                        else None,
                        value4="nap" if session.is_nap else "main",
                        source="xiaomi",
                        quality=1.0,
                        updated_at=updated_at,
                    )
                )
                if (
                    not session.is_nap
                    and session.sleep_hrv_rmssd_ms is not None
                ):
                    samples.append(
                        HealthSampleIn(
                            metric_type="SLEEP_HRV",
                            day=day,
                            bucket_start=_iso_utc(session.start_at),
                            bucket_end=_iso_utc(session.end_at),
                            value1=float(session.sleep_hrv_rmssd_ms),
                            value4="rmssd_ms",
                            source="xiaomi",
                            quality=1.0,
                            metadata={
                                "method": "device_sleep_rmssd",
                                "source_field": session.sleep_hrv_source,
                            },
                            updated_at=updated_at,
                        )
                    )

        # SpO2
        spo2_by_day: dict[str, list[float]] = defaultdict(list)
        spo2_points: list[tuple[datetime, float]] = []
        async for sample in client.iter_spo2(from_day, to_day):
            day = _day(sample.timestamp, tz)
            spo2_by_day[day].append(float(sample.spo2_pct))
            spo2_points.append((sample.timestamp, float(sample.spo2_pct)))
        metrics.extend(_daily_stat_metrics("SPO2", spo2_by_day, updated_at))
        samples.extend(_bucket_series("SPO2", spo2_points, 15, updated_at, tz))

        # Stress
        stress_by_day: dict[str, list[float]] = defaultdict(list)
        stress_points: list[tuple[datetime, float]] = []
        async for sample in client.iter_stress(from_day, to_day):
            day = _day(sample.timestamp, tz)
            stress_by_day[day].append(float(sample.stress_score))
            stress_points.append((sample.timestamp, float(sample.stress_score)))
        metrics.extend(_daily_stat_metrics("STRESS", stress_by_day, updated_at))
        samples.extend(_bucket_series("STRESS", stress_points, 15, updated_at, tz))

        # Workouts (session-shaped samples)
        async for workout in client.iter_workouts(from_day, to_day):
            samples.append(
                HealthSampleIn(
                    metric_type="WORKOUT",
                    day=_day(workout.start_at, tz),
                    bucket_start=_iso_utc(workout.start_at),
                    bucket_end=_iso_utc(workout.end_at),
                    value1=float(workout.duration_minutes),
                    value2=float(workout.calories_kcal)
                    if workout.calories_kcal is not None
                    else None,
                    value3=float(workout.avg_heart_rate_bpm)
                    if workout.avg_heart_rate_bpm is not None
                    else None,
                    value4=workout.activity_type,
                    updated_at=updated_at,
                )
            )

        # Abnormal heart beat events
        async for event in client.iter_abnormal_heart_beat(from_day, to_day):
            samples.append(
                HealthSampleIn(
                    metric_type="ABNORMAL_HEART_BEAT",
                    day=_day(event.start_at, tz),
                    bucket_start=_iso_utc(event.start_at),
                    bucket_end=_iso_utc(event.end_at),
                    value1=float(event.duration_seconds),
                    updated_at=updated_at,
                )
            )

        return metrics, samples
