from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median
from zoneinfo import ZoneInfo

from app.health.sleep_score_store import SleepScoreStore
from app.health.sleep_scoring import (
    ALGORITHM_VERSION,
    CALIBRATION_DAYS,
    NightInput,
    calibration_state,
    score_recovery,
    score_sleep_health,
)
from app.health.store import HealthStore
from app.schemas.health import HealthSampleOut, SleepScoreOut
from app.services.timezone_store import normalize_timezone


SLEEP_INPUT_TYPES = {
    "SLEEP_SESSION",
    "SLEEP_STAGE",
    "HEART_RATE",
    "RESTING_HEART_RATE",
    "SLEEP_HRV",
}


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _overlap_minutes(
    left_start: datetime,
    left_end: datetime,
    right_start: datetime,
    right_end: datetime,
) -> float:
    start = max(left_start, right_start)
    end = min(left_end, right_end)
    return max(0.0, (end - start).total_seconds() / 60.0)


@dataclass(slots=True)
class _SessionSeed:
    start_at: datetime
    end_at: datetime
    asleep_minutes: float
    vendor_score: int | None


class SleepScoreService:
    """Build canonical nights and persist deterministic dual sleep scores."""

    def __init__(self, health_store: HealthStore, score_store: SleepScoreStore) -> None:
        self.health_store = health_store
        self.score_store = score_store

    def get_scores(
        self,
        user_id: str,
        from_day: str,
        to_day: str,
    ) -> list[SleepScoreOut]:
        return self.score_store.list_scores(user_id, from_day, to_day)

    def recompute_all(self, user_id: str, tz: str = "Asia/Shanghai") -> int:
        zone = ZoneInfo(normalize_timezone(tz))
        samples = self.health_store.list_samples(user_id, SLEEP_INPUT_TYPES)
        nights = self._build_nights(samples, zone)
        if not nights:
            self.score_store.replace_scores(user_id, [])
            return 0

        computed_at = int(time.time() * 1000)
        rows: list[dict] = []
        first_day = nights[0].sleep_day
        for index, night in enumerate(nights):
            history = nights[max(0, index - 60) : index]
            valid_nights = index + 1
            state, calibration_day = calibration_state(first_day, night.sleep_day, valid_nights)
            health = score_sleep_health(night, history, state)
            recovery = score_recovery(night, history, state)
            input_summary = self._input_summary(night)
            hash_payload = {
                "algorithm_version": ALGORITHM_VERSION,
                "night": input_summary,
                "history": [self._baseline_input(item) for item in history],
            }
            input_hash = hashlib.sha256(
                json.dumps(hash_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if state == "calibrating":
                score_status = "calibrating"
            elif health.score is None and recovery.score is None:
                score_status = "insufficient_data"
            elif health.status == "ready" and recovery.status == "ready":
                score_status = "ready"
            else:
                score_status = "partial"
            rows.append(
                {
                    "sleep_day": night.sleep_day,
                    "session_start": _iso_utc(night.start_at),
                    "session_end": _iso_utc(night.end_at),
                    "sleep_health": health.as_dict(),
                    "recovery": recovery.as_dict(),
                    "calibration": {
                        "state": state,
                        "day": calibration_day,
                        "total_days": CALIBRATION_DAYS,
                        "valid_nights": valid_nights,
                    },
                    "vendor_score": night.vendor_score,
                    "score_status": score_status,
                    "input_summary": input_summary,
                    "input_hash": input_hash,
                    "computed_at": computed_at,
                }
            )

        count = self.score_store.replace_scores(user_id, rows)
        self._save_baseline(user_id, nights, rows[-1], computed_at)
        return count

    def _build_nights(
        self,
        samples: list[HealthSampleOut],
        zone: ZoneInfo,
    ) -> list[NightInput]:
        by_type: dict[str, list[HealthSampleOut]] = {}
        for sample in samples:
            by_type.setdefault(sample.metric_type, []).append(sample)

        seeds: list[_SessionSeed] = []
        for sample in by_type.get("SLEEP_SESSION", []):
            if sample.value4 != "main":
                continue
            try:
                start = _parse_utc(sample.bucket_start)
                end = _parse_utc(sample.bucket_end)
            except (TypeError, ValueError):
                continue
            if end <= start:
                continue
            window = (end - start).total_seconds() / 60.0
            if window > 24 * 60:
                continue
            asleep = float(sample.value2 or 0.0)
            if asleep <= 0:
                asleep = min(float(sample.value1 or window), window)
            seeds.append(
                _SessionSeed(
                    start_at=start,
                    end_at=end,
                    asleep_minutes=min(asleep, window),
                    vendor_score=int(round(sample.value3)) if sample.value3 is not None else None,
                )
            )

        merged = self._merge_sessions(sorted(seeds, key=lambda item: item.start_at))
        by_wake_day: dict[str, list[_SessionSeed]] = {}
        for seed in merged:
            wake_day = seed.end_at.astimezone(zone).date().isoformat()
            by_wake_day.setdefault(wake_day, []).append(seed)

        stages = by_type.get("SLEEP_STAGE", [])
        heart_rates = by_type.get("HEART_RATE", [])
        resting = by_type.get("RESTING_HEART_RATE", [])
        hrv = by_type.get("SLEEP_HRV", [])
        nights: list[NightInput] = []
        for wake_day, candidates in sorted(by_wake_day.items()):
            seed = max(candidates, key=lambda item: item.asleep_minutes)
            night = self._night_from_seed(
                seed,
                wake_day,
                stages,
                heart_rates,
                resting,
                hrv,
                zone,
            )
            if night.asleep_minutes >= 180:
                nights.append(night)
        return nights

    @staticmethod
    def _merge_sessions(seeds: list[_SessionSeed]) -> list[_SessionSeed]:
        merged: list[_SessionSeed] = []
        for seed in seeds:
            if not merged:
                merged.append(seed)
                continue
            current = merged[-1]
            gap = (seed.start_at - current.end_at).total_seconds() / 60.0
            if -60 <= gap <= 60:
                current.end_at = max(current.end_at, seed.end_at)
                current.asleep_minutes += seed.asleep_minutes
                current.asleep_minutes = min(
                    current.asleep_minutes,
                    (current.end_at - current.start_at).total_seconds() / 60.0,
                )
                if seed.vendor_score is not None:
                    current.vendor_score = max(current.vendor_score or 0, seed.vendor_score)
            else:
                merged.append(seed)
        return merged

    def _night_from_seed(
        self,
        seed: _SessionSeed,
        wake_day: str,
        stage_samples: list[HealthSampleOut],
        heart_samples: list[HealthSampleOut],
        resting_samples: list[HealthSampleOut],
        hrv_samples: list[HealthSampleOut],
        zone: ZoneInfo,
    ) -> NightInput:
        window = (seed.end_at - seed.start_at).total_seconds() / 60.0
        stage_intervals: list[tuple[datetime, datetime, int, float]] = []
        for sample in stage_samples:
            try:
                start = _parse_utc(sample.bucket_start)
                end = _parse_utc(sample.bucket_end)
            except (TypeError, ValueError):
                continue
            overlap = _overlap_minutes(seed.start_at, seed.end_at, start, end)
            if overlap <= 0 or sample.value1 is None:
                continue
            stage_intervals.append((start, end, int(round(sample.value1)), overlap))
        stage_coverage = min(1.0, sum(item[3] for item in stage_intervals) / max(window, 1.0))
        deep = sum(item[3] for item in stage_intervals if item[2] == 1)
        rem = sum(item[3] for item in stage_intervals if item[2] == 3)
        stage_asleep = sum(item[3] for item in stage_intervals if item[2] in {1, 2, 3})
        asleep = stage_asleep if stage_coverage >= 0.70 else seed.asleep_minutes
        asleep = min(max(asleep, 0.0), window)
        awake = max(0.0, window - asleep)
        awakenings = self._count_awakenings(stage_intervals)

        heart: list[tuple[datetime, float]] = []
        covered_minutes = 0.0
        for sample in heart_samples:
            if sample.value1 is None or not (30 <= sample.value1 <= 220):
                continue
            try:
                start = _parse_utc(sample.bucket_start)
                end = _parse_utc(sample.bucket_end)
            except (TypeError, ValueError):
                continue
            overlap = _overlap_minutes(seed.start_at, seed.end_at, start, end)
            if overlap <= 0:
                continue
            heart.append((max(start, seed.start_at), float(sample.value1)))
            covered_minutes += overlap
        heart.sort(key=lambda item: item[0])
        heart_coverage = min(1.0, covered_minutes / max(window, 1.0))
        stable_nadir, nadir_at = self._stable_nadir(heart)
        midpoint = seed.start_at + (seed.end_at - seed.start_at) / 2
        first_values = [value for timestamp, value in heart if timestamp < midpoint]
        second_values = [value for timestamp, value in heart if timestamp >= midpoint]

        rhr_points: list[tuple[datetime, float]] = []
        for sample in resting_samples:
            if sample.value1 is None:
                continue
            try:
                timestamp = _parse_utc(sample.bucket_start)
            except (TypeError, ValueError):
                continue
            if seed.start_at <= timestamp <= seed.end_at and 30 <= sample.value1 <= 220:
                rhr_points.append((timestamp, float(sample.value1)))
        device_rhr_at = None
        device_rhr = None
        if rhr_points:
            device_rhr_at, device_rhr = min(rhr_points, key=lambda item: item[1])

        hrv_values: list[tuple[float, float]] = []
        for sample in hrv_samples:
            if sample.value1 is None or not (5 <= sample.value1 <= 300):
                continue
            try:
                start = _parse_utc(sample.bucket_start)
                end = _parse_utc(sample.bucket_end)
            except (TypeError, ValueError):
                continue
            if _overlap_minutes(seed.start_at, seed.end_at, start, end) <= 0:
                continue
            hrv_values.append((float(sample.value1), float(sample.quality or 1.0)))

        return NightInput(
            sleep_day=wake_day,
            # Scoring regularity is based on local clock time. All comparisons
            # remain safe because the timestamps are timezone-aware, while API
            # persistence converts them back to UTC through `_iso_utc`.
            start_at=seed.start_at.astimezone(zone),
            end_at=seed.end_at.astimezone(zone),
            duration_minutes=window,
            asleep_minutes=asleep,
            awake_minutes=awake,
            awakenings=awakenings,
            deep_minutes=deep,
            rem_minutes=rem,
            stage_coverage=stage_coverage,
            heart_rates=heart,
            heart_rate_coverage=heart_coverage,
            stable_hr_nadir=stable_nadir,
            nadir_at=nadir_at,
            first_half_hr=float(median(first_values)) if first_values else None,
            second_half_hr=float(median(second_values)) if second_values else None,
            device_rhr=device_rhr,
            device_rhr_at=device_rhr_at,
            sleep_hrv_rmssd=float(median([value for value, _ in hrv_values]))
            if hrv_values
            else None,
            hrv_quality=float(median([quality for _, quality in hrv_values]))
            if hrv_values
            else 0.0,
            vendor_score=seed.vendor_score,
        )

    @staticmethod
    def _count_awakenings(intervals: list[tuple[datetime, datetime, int, float]]) -> int:
        awake = sorted((start, end) for start, end, stage, _ in intervals if stage == 4)
        if not awake:
            return 0
        merged: list[list[datetime]] = []
        for start, end in awake:
            if not merged or start > merged[-1][1] + timedelta(minutes=1):
                merged.append([start, end])
            else:
                merged[-1][1] = max(merged[-1][1], end)
        return sum(1 for start, end in merged if (end - start).total_seconds() >= 5 * 60)

    @staticmethod
    def _stable_nadir(
        heart: list[tuple[datetime, float]],
    ) -> tuple[float | None, datetime | None]:
        if not heart:
            return None, None
        if len(heart) == 1:
            return heart[0][1], heart[0][0]
        windows: list[tuple[float, datetime]] = []
        for index in range(len(heart) - 1):
            block = heart[index : index + 2]
            if (block[-1][0] - block[0][0]).total_seconds() > 45 * 60:
                continue
            windows.append((float(median([value for _, value in block])), block[-1][0]))
        if not windows:
            return min(heart, key=lambda item: item[1])[1], min(heart, key=lambda item: item[1])[0]
        value, timestamp = min(windows, key=lambda item: (item[0], item[1]))
        return value, timestamp

    @staticmethod
    def _input_summary(night: NightInput) -> dict:
        return {
            "sleep_day": night.sleep_day,
            "session_start": _iso_utc(night.start_at),
            "session_end": _iso_utc(night.end_at),
            "duration_minutes": round(night.duration_minutes, 2),
            "asleep_minutes": round(night.asleep_minutes, 2),
            "awake_minutes": round(night.awake_minutes, 2),
            "awakenings": night.awakenings,
            "deep_minutes": round(night.deep_minutes, 2),
            "rem_minutes": round(night.rem_minutes, 2),
            "stage_coverage": round(night.stage_coverage, 4),
            "heart_rate_coverage": round(night.heart_rate_coverage, 4),
            "heart_rate_points": len(night.heart_rates),
            "stable_hr_nadir": round(night.stable_hr_nadir, 2)
            if night.stable_hr_nadir is not None
            else None,
            "nadir_at": _iso_utc(night.nadir_at) if night.nadir_at else None,
            "device_rhr": night.device_rhr,
            "device_rhr_at": _iso_utc(night.device_rhr_at) if night.device_rhr_at else None,
            "sleep_hrv_rmssd": night.sleep_hrv_rmssd,
            "vendor_score": night.vendor_score,
        }

    @staticmethod
    def _baseline_input(night: NightInput) -> dict:
        values = [value for _, value in night.heart_rates]
        return {
            "sleep_day": night.sleep_day,
            "asleep_minutes": round(night.asleep_minutes, 2),
            "start_minute": night.start_at.hour * 60 + night.start_at.minute,
            "end_minute": night.end_at.hour * 60 + night.end_at.minute,
            "heart_rate_median": round(float(median(values)), 2) if values else None,
            "stable_hr_nadir": night.stable_hr_nadir,
            "sleep_hrv_rmssd": night.sleep_hrv_rmssd,
        }

    def _save_baseline(
        self,
        user_id: str,
        nights: list[NightInput],
        latest_row: dict,
        updated_at: int,
    ) -> None:
        recent = nights[-60:]
        hr_nights = [item for item in recent if item.heart_rate_coverage >= 0.70]
        hrv_nights = [item for item in recent if item.sleep_hrv_rmssd is not None]
        asleep = [item.asleep_minutes for item in recent]
        hr_medians = [
            float(median([value for _, value in item.heart_rates]))
            for item in hr_nights
            if item.heart_rates
        ]
        nadirs = [item.stable_hr_nadir for item in hr_nights if item.stable_hr_nadir is not None]
        hrv = [item.sleep_hrv_rmssd for item in hrv_nights if item.sleep_hrv_rmssd is not None]
        start = date.fromisoformat(nights[0].sleep_day)
        calibration_end = start + timedelta(days=CALIBRATION_DAYS - 1)
        baseline = {
            "asleep_minutes_median": float(median(asleep)) if asleep else None,
            "night_hr_median": float(median(hr_medians)) if hr_medians else None,
            "stable_hr_nadir_median": float(median(nadirs)) if nadirs else None,
            "sleep_hrv_rmssd_median": float(median(hrv)) if hrv else None,
        }
        self.score_store.save_baseline(
            user_id,
            state=latest_row["calibration"]["state"],
            calibration_start=nights[0].sleep_day,
            calibration_end=calibration_end.isoformat(),
            valid_nights={
                "main_sleep": len(nights),
                "heart_rate": len(hr_nights),
                "sleep_hrv": len(hrv_nights),
            },
            baseline=baseline,
            window_start=recent[0].sleep_day,
            window_end=recent[-1].sleep_day,
            updated_at=updated_at,
        )
