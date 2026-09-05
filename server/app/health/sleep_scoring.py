from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from statistics import median
from typing import Any, Iterable


ALGORITHM_VERSION = "sleep_dual_v1"
CALIBRATION_DAYS = 14
MIN_BASELINE_NIGHTS = 7


@dataclass(slots=True)
class NightInput:
    sleep_day: str
    start_at: datetime
    end_at: datetime
    duration_minutes: float
    asleep_minutes: float
    awake_minutes: float
    awakenings: int
    deep_minutes: float
    rem_minutes: float
    stage_coverage: float
    heart_rates: list[tuple[datetime, float]] = field(default_factory=list)
    heart_rate_coverage: float = 0.0
    stable_hr_nadir: float | None = None
    nadir_at: datetime | None = None
    first_half_hr: float | None = None
    second_half_hr: float | None = None
    device_rhr: float | None = None
    device_rhr_at: datetime | None = None
    sleep_hrv_rmssd: float | None = None
    hrv_quality: float = 0.0
    vendor_score: int | None = None


@dataclass(slots=True)
class ComponentResult:
    score: int
    weight: float
    quality: float
    value: dict[str, Any]
    reason: str | None = None

    def as_dict(self, personal_factor: float = 1.0) -> dict[str, Any]:
        return {
            "score": self.score,
            "weight": self.weight,
            "effective_weight": round(self.weight * personal_factor * self.quality, 4),
            "quality": round(self.quality, 4),
            "value": self.value,
            "reason": self.reason,
        }


@dataclass(slots=True)
class DimensionResult:
    score: int | None
    confidence: int
    status: str
    components: dict[str, dict[str, Any]]
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "confidence": self.confidence,
            "status": self.status,
            "components": self.components,
            "missing": self.missing,
        }


def _clip(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _piecewise(value: float, points: list[tuple[float, float]]) -> float:
    ordered = sorted(points)
    if value <= ordered[0][0]:
        return ordered[0][1]
    if value >= ordered[-1][0]:
        return ordered[-1][1]
    for (left_x, left_y), (right_x, right_y) in zip(ordered, ordered[1:]):
        if left_x <= value <= right_x:
            ratio = (value - left_x) / (right_x - left_x)
            return left_y + ratio * (right_y - left_y)
    return ordered[-1][1]


def _safe_median(values: Iterable[float]) -> float | None:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    return float(median(clean)) if clean else None


def _mad(values: list[float], center: float) -> float:
    raw = _safe_median(abs(value - center) for value in values)
    return float(raw or 0.0)


def _circular_center(minutes: list[float]) -> float | None:
    if not minutes:
        return None
    angles = [value / 1440.0 * 2.0 * math.pi for value in minutes]
    x = sum(math.cos(angle) for angle in angles)
    y = sum(math.sin(angle) for angle in angles)
    if x == 0 and y == 0:
        return float(median(minutes))
    angle = math.atan2(y, x)
    if angle < 0:
        angle += 2.0 * math.pi
    return angle / (2.0 * math.pi) * 1440.0


def _circular_distance(left: float, right: float) -> float:
    direct = abs(left - right) % 1440.0
    return min(direct, 1440.0 - direct)


def _minute_of_day(value: datetime) -> float:
    return value.hour * 60.0 + value.minute + value.second / 60.0


def _deviation_score(minutes: float) -> float:
    return _piecewise(
        minutes,
        [(0, 100), (30, 95), (60, 80), (90, 65), (120, 45), (180, 20), (300, 0)],
    )


def _bounded_factor(name: str, personal_factors: dict[str, float] | None) -> float:
    if not personal_factors:
        return 1.0
    return _clip(float(personal_factors.get(name, 1.0)), 0.8, 1.2)


def _combine(
    components: dict[str, ComponentResult],
    total_base_weight: float,
    personal_factors: dict[str, float] | None,
) -> tuple[int | None, int, dict[str, dict[str, Any]]]:
    numerator = 0.0
    denominator = 0.0
    rendered: dict[str, dict[str, Any]] = {}
    quality_weight = 0.0
    for name, component in components.items():
        factor = _bounded_factor(name, personal_factors)
        effective = component.weight * factor * _clip(component.quality, 0.0, 1.0)
        numerator += component.score * effective
        denominator += effective
        quality_weight += component.weight * _clip(component.quality, 0.0, 1.0)
        rendered[name] = component.as_dict(factor)
    if denominator <= 0:
        return None, 0, rendered
    score = int(round(_clip(numerator / denominator, 0.0, 100.0)))
    confidence = int(round(_clip(100.0 * quality_weight / total_base_weight, 0.0, 100.0)))
    return score, confidence, rendered


def calibration_state(
    first_sleep_day: str,
    sleep_day: str,
    valid_nights: int,
) -> tuple[str, int]:
    from datetime import date

    first = date.fromisoformat(first_sleep_day)
    current = date.fromisoformat(sleep_day)
    day = max(1, (current - first).days + 1)
    if day <= CALIBRATION_DAYS:
        return "calibrating", day
    if valid_nights >= MIN_BASELINE_NIGHTS:
        return "calibrated", CALIBRATION_DAYS
    return "insufficient_data", CALIBRATION_DAYS


def score_sleep_health(
    night: NightInput,
    history: list[NightInput],
    calibration: str,
    personal_factors: dict[str, float] | None = None,
) -> DimensionResult:
    if night.asleep_minutes < 180 or night.end_at <= night.start_at:
        return DimensionResult(None, 0, "insufficient_data", {}, ["valid_main_sleep"])

    prior_asleep = [item.asleep_minutes for item in history if item.asleep_minutes >= 180]
    target = 480.0
    if len(prior_asleep) >= MIN_BASELINE_NIGHTS:
        target = _clip(float(median(prior_asleep[-60:])), 420.0, 540.0)
    duration_points = {
        180.0: 10.0,
        240.0: 25.0,
        300.0: 45.0,
        360.0: 70.0,
        420.0: 90.0,
        target: 100.0,
        target + 60.0: 96.0,
        max(target + 120.0, 600.0): 85.0,
        720.0: 65.0,
    }
    duration_score = _piecewise(night.asleep_minutes, list(duration_points.items()))

    window = max(night.duration_minutes, 1.0)
    efficiency = _clip(night.asleep_minutes / window, 0.0, 1.0)
    efficiency_score = _piecewise(
        efficiency,
        [(0.50, 10), (0.60, 30), (0.75, 60), (0.85, 85), (0.90, 95), (0.95, 100), (1.0, 100)],
    )
    waso_score = _piecewise(
        night.awake_minutes,
        [(0, 100), (20, 95), (40, 80), (60, 60), (90, 35), (120, 15), (180, 0)],
    )
    awakening_score = _piecewise(
        float(night.awakenings),
        [(0, 100), (1, 95), (2, 85), (3, 70), (5, 40), (8, 10), (12, 0)],
    )
    continuity = 0.5 * efficiency_score + 0.3 * waso_score + 0.2 * awakening_score
    continuity_quality = 1.0 if night.stage_coverage >= 0.70 else 0.80

    components: dict[str, ComponentResult] = {
        "duration": ComponentResult(
            int(round(duration_score)),
            0.40,
            1.0,
            {"asleep_minutes": round(night.asleep_minutes, 1), "target_minutes": round(target, 1)},
        ),
        "continuity": ComponentResult(
            int(round(continuity)),
            0.25,
            continuity_quality,
            {
                "efficiency": round(efficiency, 4),
                "awake_minutes": round(night.awake_minutes, 1),
                "awakenings": night.awakenings,
            },
            None if night.stage_coverage >= 0.70 else "sleep_stages_incomplete",
        ),
    }
    missing: list[str] = []

    timing_history = [item for item in history if item.end_at > item.start_at][-60:]
    if len(timing_history) >= 3:
        bed_center = _circular_center([_minute_of_day(item.start_at) for item in timing_history])
        wake_center = _circular_center([_minute_of_day(item.end_at) for item in timing_history])
        midpoint_center = _circular_center(
            [_minute_of_day(item.start_at + (item.end_at - item.start_at) / 2) for item in timing_history]
        )
        midpoint = night.start_at + (night.end_at - night.start_at) / 2
        assert bed_center is not None and wake_center is not None and midpoint_center is not None
        bed_delta = _circular_distance(_minute_of_day(night.start_at), bed_center)
        wake_delta = _circular_distance(_minute_of_day(night.end_at), wake_center)
        midpoint_delta = _circular_distance(_minute_of_day(midpoint), midpoint_center)
        regularity = (
            0.4 * _deviation_score(bed_delta)
            + 0.4 * _deviation_score(wake_delta)
            + 0.2 * _deviation_score(midpoint_delta)
        )
        components["regularity"] = ComponentResult(
            int(round(regularity)),
            0.20,
            min(1.0, len(timing_history) / MIN_BASELINE_NIGHTS),
            {
                "bedtime_deviation_minutes": round(bed_delta, 1),
                "wake_deviation_minutes": round(wake_delta, 1),
                "midpoint_deviation_minutes": round(midpoint_delta, 1),
            },
        )
    else:
        missing.append("sleep_regularity_baseline")

    asleep_stage = night.deep_minutes + night.rem_minutes
    if night.stage_coverage >= 0.70 and asleep_stage > 0 and night.asleep_minutes > 0:
        deep_pct = 100.0 * night.deep_minutes / night.asleep_minutes
        rem_pct = 100.0 * night.rem_minutes / night.asleep_minutes
        deep_score = _piecewise(
            deep_pct,
            [(0, 20), (5, 60), (10, 95), (15, 100), (25, 100), (35, 70), (50, 30)],
        )
        rem_score = _piecewise(
            rem_pct,
            [(0, 20), (10, 70), (15, 95), (20, 100), (30, 100), (40, 70), (60, 20)],
        )
        components["architecture"] = ComponentResult(
            int(round((deep_score + rem_score) / 2.0)),
            0.15,
            _clip(night.stage_coverage, 0.0, 1.0),
            {
                "deep_minutes": round(night.deep_minutes, 1),
                "rem_minutes": round(night.rem_minutes, 1),
                "stage_coverage": round(night.stage_coverage, 4),
            },
        )
    else:
        missing.append("sleep_architecture")

    score, confidence, rendered = _combine(components, 1.0, personal_factors)
    if score is not None:
        if night.asleep_minutes < 240:
            score = min(score, 40)
        elif night.asleep_minutes < 300:
            score = min(score, 55)
        elif night.asleep_minutes < 360:
            score = min(score, 70)
        elif night.asleep_minutes < 420:
            score = min(score, 85)
    if calibration == "calibrating":
        confidence = min(confidence, 69)
        status = "calibrating"
    elif calibration == "insufficient_data":
        status = "partial"
    else:
        status = "ready" if confidence >= 70 and not missing else "partial"
    return DimensionResult(score, confidence, status, rendered, missing)


def _night_hr_score(current: float, baseline: float | None) -> float:
    if baseline is None:
        return _piecewise(
            current,
            [(35, 55), (45, 80), (55, 90), (65, 85), (80, 70), (100, 40), (130, 0)],
        )
    delta = current - baseline
    if -5.0 <= delta <= 0.0:
        return 100.0
    if delta > 0:
        return _clip(100.0 - delta * 6.0, 0.0, 100.0)
    return _clip(100.0 - (abs(delta) - 5.0) * 4.0, 20.0, 100.0)


def score_recovery(
    night: NightInput,
    history: list[NightInput],
    calibration: str,
    personal_factors: dict[str, float] | None = None,
) -> DimensionResult:
    heart_values = [value for _, value in night.heart_rates if 30 <= value <= 220]
    if night.heart_rate_coverage < 0.70 or len(heart_values) < 8:
        return DimensionResult(None, 0, "insufficient_data", {}, ["night_heart_rate"])

    current_median = float(median(heart_values))
    heart_history = [
        item
        for item in history[-60:]
        if item.heart_rate_coverage >= 0.70 and len(item.heart_rates) >= 8
    ]
    history_medians = [float(median([value for _, value in item.heart_rates])) for item in heart_history]
    history_nadirs = [item.stable_hr_nadir for item in heart_history if item.stable_hr_nadir is not None]
    history_rhr = [item.device_rhr for item in heart_history if item.device_rhr is not None]
    baseline_hr = float(median(history_medians)) if len(history_medians) >= MIN_BASELINE_NIGHTS else None
    baseline_nadir = float(median(history_nadirs)) if len(history_nadirs) >= MIN_BASELINE_NIGHTS else None
    baseline_rhr = float(median(history_rhr)) if len(history_rhr) >= MIN_BASELINE_NIGHTS else None
    median_score = _night_hr_score(current_median, baseline_hr)
    nadir_score = (
        _night_hr_score(night.stable_hr_nadir, baseline_nadir)
        if night.stable_hr_nadir is not None
        else median_score
    )
    heart_score_parts = [(median_score, 0.65), (nadir_score, 0.25)]
    device_rhr_score = None
    if night.device_rhr is not None:
        device_rhr_score = _night_hr_score(night.device_rhr, baseline_rhr)
        heart_score_parts.append((device_rhr_score, 0.10))
    heart_score = sum(value * weight for value, weight in heart_score_parts) / sum(
        weight for _, weight in heart_score_parts
    )
    hr_quality = _clip(night.heart_rate_coverage, 0.0, 1.0)
    if baseline_hr is None:
        hr_quality *= 0.5

    rhr_difference = None
    rhr_position = None
    heart_reason = None if baseline_hr is not None else "personal_baseline_incomplete"
    if night.device_rhr is not None and night.stable_hr_nadir is not None:
        rhr_difference = abs(night.device_rhr - night.stable_hr_nadir)
        if rhr_difference > 20:
            hr_quality *= 0.50
            heart_reason = "device_rhr_mismatch"
        elif rhr_difference > 10:
            hr_quality *= 0.75
            heart_reason = "device_rhr_mismatch"
    if night.device_rhr_at is not None:
        rhr_elapsed = (night.device_rhr_at - night.start_at).total_seconds()
        rhr_position = _clip(
            rhr_elapsed / max((night.end_at - night.start_at).total_seconds(), 1.0),
            0.0,
            1.0,
        )

    components: dict[str, ComponentResult] = {
        "night_heart_rate": ComponentResult(
            int(round(heart_score)),
            0.30,
            hr_quality,
            {
                "median_bpm": round(current_median, 1),
                "stable_nadir_bpm": round(night.stable_hr_nadir, 1)
                if night.stable_hr_nadir is not None
                else None,
                "baseline_median_bpm": round(baseline_hr, 1) if baseline_hr is not None else None,
                "device_rhr_bpm": round(night.device_rhr, 1) if night.device_rhr is not None else None,
                "device_rhr_at": night.device_rhr_at.isoformat() if night.device_rhr_at else None,
                "device_rhr_position": round(rhr_position, 4) if rhr_position is not None else None,
                "device_rhr_baseline_bpm": round(baseline_rhr, 1)
                if baseline_rhr is not None
                else None,
                "device_rhr_nadir_difference_bpm": round(rhr_difference, 1)
                if rhr_difference is not None
                else None,
                "device_rhr_score": round(device_rhr_score, 1)
                if device_rhr_score is not None
                else None,
            },
            heart_reason,
        )
    }
    missing: list[str] = []

    if night.nadir_at is not None:
        elapsed = (night.nadir_at - night.start_at).total_seconds() / 60.0
        position = _clip(elapsed / max(night.duration_minutes, 1.0), 0.0, 1.0)
        after_minutes = max(0.0, (night.end_at - night.nadir_at).total_seconds() / 60.0)
        group_timing_score = _piecewise(
            position,
            [(0.0, 95), (0.15, 100), (0.45, 100), (0.60, 82), (0.75, 62), (0.90, 40), (1.0, 25)],
        )
        history_timing = [
            (
                _clip(
                    (item.nadir_at - item.start_at).total_seconds()
                    / max((item.end_at - item.start_at).total_seconds(), 1.0),
                    0.0,
                    1.0,
                ),
                max(0.0, (item.end_at - item.nadir_at).total_seconds() / 60.0),
            )
            for item in heart_history
            if item.nadir_at is not None and item.end_at > item.start_at
        ]
        baseline_position = (
            float(median([value[0] for value in history_timing]))
            if len(history_timing) >= MIN_BASELINE_NIGHTS
            else None
        )
        baseline_after_minutes = (
            float(median([value[1] for value in history_timing]))
            if len(history_timing) >= MIN_BASELINE_NIGHTS
            else None
        )
        personal_timing_score = None
        if baseline_position is not None:
            deviation = abs(position - baseline_position)
            personal_timing_score = _piecewise(
                deviation,
                [(0.0, 100), (0.10, 95), (0.20, 82), (0.35, 58), (0.50, 30), (1.0, 0)],
            )
            timing_score = 0.60 * group_timing_score + 0.40 * personal_timing_score
        else:
            timing_score = group_timing_score
        timing_quality = _clip(night.heart_rate_coverage, 0.0, 1.0)
        timing_reason = None if baseline_position is not None else "personal_baseline_incomplete"
        if rhr_position is not None and abs(rhr_position - position) > 0.35:
            timing_quality *= 0.80
            timing_reason = "device_rhr_time_mismatch"
        components["nadir_timing"] = ComponentResult(
            int(round(timing_score)),
            0.20,
            timing_quality,
            {
                "nadir_at": night.nadir_at.isoformat(),
                "nadir_position": round(position, 4),
                "sleep_after_nadir_minutes": round(after_minutes, 1),
                "baseline_nadir_position": round(baseline_position, 4)
                if baseline_position is not None
                else None,
                "baseline_sleep_after_nadir_minutes": round(baseline_after_minutes, 1)
                if baseline_after_minutes is not None
                else None,
                "personal_timing_score": round(personal_timing_score, 1)
                if personal_timing_score is not None
                else None,
            },
            timing_reason,
        )
    else:
        missing.append("heart_rate_nadir")

    if night.first_half_hr is not None and night.second_half_hr is not None:
        half_delta = night.second_half_hr - night.first_half_hr
        trend_score = _clip(100.0 - abs(half_delta) * 5.0, 30.0, 100.0)
        components["heart_rate_trend"] = ComponentResult(
            int(round(trend_score)),
            0.10,
            _clip(night.heart_rate_coverage, 0.0, 1.0),
            {
                "first_half_bpm": round(night.first_half_hr, 1),
                "second_half_bpm": round(night.second_half_hr, 1),
                "difference_bpm": round(half_delta, 1),
            },
        )
    else:
        missing.append("heart_rate_trend")

    if night.sleep_hrv_rmssd is not None and night.sleep_hrv_rmssd > 0:
        current_ln = math.log(night.sleep_hrv_rmssd)
        hrv_history = [
            math.log(item.sleep_hrv_rmssd)
            for item in history[-60:]
            if item.sleep_hrv_rmssd is not None and item.sleep_hrv_rmssd > 0
        ]
        if len(hrv_history) >= MIN_BASELINE_NIGHTS:
            center = float(median(hrv_history))
            scale = max(1.4826 * _mad(hrv_history, center), 0.08)
            z_score = (current_ln - center) / scale
            hrv_score = 100.0 - max(0.0, -z_score) * 20.0 - max(0.0, z_score - 2.0) * 5.0
            hrv_quality = _clip(night.hrv_quality, 0.0, 1.0)
            reason = None
        else:
            center = None
            z_score = None
            hrv_score = 75.0
            hrv_quality = 0.5 * _clip(night.hrv_quality, 0.0, 1.0)
            reason = "personal_baseline_incomplete"
        components["sleep_hrv"] = ComponentResult(
            int(round(_clip(hrv_score, 0.0, 100.0))),
            0.40,
            hrv_quality,
            {
                "rmssd_ms": round(night.sleep_hrv_rmssd, 2),
                "ln_rmssd": round(current_ln, 4),
                "baseline_ln_rmssd": round(center, 4) if center is not None else None,
                "robust_z": round(z_score, 3) if z_score is not None else None,
            },
            reason,
        )
    else:
        missing.append("sleep_hrv")

    score, confidence, rendered = _combine(components, 1.0, personal_factors)
    if "sleep_hrv" in missing:
        confidence = min(confidence, 60)
    if calibration == "calibrating":
        confidence = min(confidence, 69)
        status = "calibrating"
    elif missing or baseline_hr is None or calibration == "insufficient_data":
        status = "partial"
    else:
        status = "ready" if confidence >= 70 else "partial"
    return DimensionResult(score, confidence, status, rendered, missing)
