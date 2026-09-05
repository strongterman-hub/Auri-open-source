from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.health.sleep_scoring import NightInput, score_recovery, score_sleep_health


def _night(
    day: int,
    *,
    asleep: float = 450,
    with_hr: bool = True,
    hrv: float | None = None,
    rhr: float | None = None,
) -> NightInput:
    start = datetime(2026, 8, day, 23, 0, tzinfo=UTC)
    end = start + timedelta(hours=8)
    heart = []
    if with_hr:
        heart = [
            (start + timedelta(minutes=15 * index), 62.0 - min(index, 12) * 0.3)
            for index in range(32)
        ]
    return NightInput(
        sleep_day=(end.date()).isoformat(),
        start_at=start,
        end_at=end,
        duration_minutes=480,
        asleep_minutes=asleep,
        awake_minutes=max(0, 480 - asleep),
        awakenings=2,
        deep_minutes=90,
        rem_minutes=95,
        stage_coverage=1.0,
        heart_rates=heart,
        heart_rate_coverage=1.0 if heart else 0.0,
        stable_hr_nadir=58.4 if heart else None,
        nadir_at=start + timedelta(hours=3) if heart else None,
        first_half_hr=60.5 if heart else None,
        second_half_hr=59.0 if heart else None,
        device_rhr=rhr,
        device_rhr_at=start + timedelta(hours=3) if rhr is not None else None,
        sleep_hrv_rmssd=hrv,
        hrv_quality=1.0 if hrv is not None else 0.0,
    )


def test_short_sleep_cannot_receive_high_health_score() -> None:
    night = _night(1, asleep=230)
    result = score_sleep_health(night, [], "calibrating")
    assert result.score is not None
    assert result.score <= 40


def test_calibrating_health_confidence_is_capped() -> None:
    history = [_night(day) for day in range(1, 8)]
    result = score_sleep_health(_night(8), history, "calibrating")
    assert result.score is not None
    assert result.confidence == 69
    assert result.status == "calibrating"


def test_recovery_without_hrv_is_partial_and_low_confidence() -> None:
    history = [_night(day) for day in range(1, 8)]
    result = score_recovery(_night(8), history, "calibrated")
    assert result.score is not None
    assert result.status == "partial"
    assert result.confidence <= 60
    assert "sleep_hrv" in result.missing


def test_recovery_never_invents_hrv_from_average_heart_rate() -> None:
    result = score_recovery(_night(1), [], "calibrating")
    assert "sleep_hrv" not in result.components
    assert "sleep_hrv" in result.missing


def test_recovery_requires_night_heart_rate_coverage() -> None:
    result = score_recovery(_night(1, with_hr=False), [], "calibrating")
    assert result.score is None
    assert result.status == "insufficient_data"


def test_recovery_with_hrv_and_personal_baseline_can_be_ready() -> None:
    history = [_night(day, hrv=48 + day) for day in range(1, 8)]
    result = score_recovery(_night(8, hrv=54), history, "calibrated")
    assert result.score is not None
    assert result.status == "ready"
    assert result.confidence >= 70
    assert "sleep_hrv" in result.components


def test_personal_factors_are_bounded() -> None:
    night = _night(8)
    history = [_night(day) for day in range(1, 8)]
    result = score_sleep_health(
        night,
        history,
        "calibrated",
        personal_factors={"duration": 9.0, "continuity": 0.01},
    )
    assert result.components["duration"]["effective_weight"] == 0.48
    assert result.components["continuity"]["effective_weight"] == 0.2


def test_recovery_uses_device_rhr_value_and_occurrence_time() -> None:
    history = [_night(day, rhr=58.0) for day in range(1, 8)]
    result = score_recovery(_night(8, rhr=60.0), history, "calibrated")

    heart = result.components["night_heart_rate"]
    assert heart["value"]["device_rhr_bpm"] == 60.0
    assert heart["value"]["device_rhr_baseline_bpm"] == 58.0
    assert heart["value"]["device_rhr_position"] == 0.375
    assert heart["value"]["device_rhr_score"] is not None
    assert result.components["nadir_timing"]["value"]["baseline_nadir_position"] == 0.375
