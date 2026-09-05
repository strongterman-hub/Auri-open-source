from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.schemas.health import HealthMetricOut


def _numbers(values: list[Any]) -> list[float]:
    return [float(value) for value in values if value is not None]


def _stats(values: list[Any]) -> dict[str, float] | None:
    numbers = _numbers(values)
    if not numbers:
        return None
    return {
        "count": len(numbers),
        "sum": round(sum(numbers), 2),
        "avg": round(sum(numbers) / len(numbers), 2),
        "min": round(min(numbers), 2),
        "max": round(max(numbers), 2),
    }


def _by_type(metrics: list[HealthMetricOut]) -> dict[str, list[HealthMetricOut]]:
    grouped: dict[str, list[HealthMetricOut]] = defaultdict(list)
    for metric in metrics:
        grouped[metric.metric_type].append(metric)
    return grouped


def summary(
    metrics: list[HealthMetricOut],
    from_day: str,
    to_day: str,
) -> dict[str, Any]:
    """Compute per-metric aggregates plus a few human-friendly highlights."""
    metrics_out: dict[str, Any] = {}
    for metric_type, entries in sorted(_by_type(metrics).items()):
        ordered = sorted(entries, key=lambda entry: entry.day)
        latest = ordered[-1]
        metrics_out[metric_type] = {
            "days": len(ordered),
            "value1": _stats([entry.value1 for entry in ordered]),
            "value2": _stats([entry.value2 for entry in ordered]),
            "value3": _stats([entry.value3 for entry in ordered]),
            "latest_day": latest.day,
            "latest": {
                "value1": latest.value1,
                "value2": latest.value2,
                "value3": latest.value3,
            },
        }

    highlights: dict[str, Any] = {}
    steps = metrics_out.get("STEPS", {}).get("value1")
    if steps:
        highlights["steps_total"] = steps["sum"]
        highlights["steps_avg"] = steps["avg"]
    calories = metrics_out.get("CALORIES", {}).get("value1")
    if calories:
        highlights["calories_total"] = calories["sum"]
    distance = metrics_out.get("DISTANCE", {}).get("value1")
    if distance:
        highlights["distance_total_m"] = distance["sum"]
    sleep_score = metrics_out.get("SLEEP", {}).get("value3")
    if sleep_score:
        highlights["sleep_score_avg"] = sleep_score["avg"]
    sleep_duration = metrics_out.get("SLEEP", {}).get("value1")
    if sleep_duration:
        highlights["sleep_duration_avg_min"] = sleep_duration["avg"]
    resting_hr = metrics_out.get("RESTING_HEART_RATE", {}).get("value1")
    if resting_hr:
        highlights["resting_heart_rate_avg"] = resting_hr["avg"]
    weight = metrics_out.get("WEIGHT", {}).get("latest")
    if weight and weight.get("value1") is not None:
        highlights["weight_latest_kg"] = weight["value1"]

    return {
        "from_day": from_day,
        "to_day": to_day,
        "metrics": metrics_out,
        "highlights": highlights,
    }


def trend(
    metrics: list[HealthMetricOut],
    metric_type: str,
    from_day: str,
    to_day: str,
) -> dict[str, Any]:
    """Return the day-by-day series for one daily metric plus simple stats."""
    entries = sorted(
        (entry for entry in metrics if entry.metric_type == metric_type),
        key=lambda entry: entry.day,
    )
    series = [
        {
            "day": entry.day,
            "value1": entry.value1,
            "value2": entry.value2,
            "value3": entry.value3,
        }
        for entry in entries
    ]
    return {
        "metric_type": metric_type,
        "from_day": from_day,
        "to_day": to_day,
        "series": series,
        "stats": _stats([entry.value1 for entry in entries]),
    }


def compare(
    metrics: list[HealthMetricOut],
    metric_type: str,
    a_from: str,
    a_to: str,
    b_from: str,
    b_to: str,
) -> dict[str, Any]:
    """Compare value1 aggregates between two date windows for one metric."""
    entries = [entry for entry in metrics if entry.metric_type == metric_type]
    a_entries = [entry for entry in entries if a_from <= entry.day <= a_to]
    b_entries = [entry for entry in entries if b_from <= entry.day <= b_to]
    a_stats = _stats([entry.value1 for entry in a_entries])
    b_stats = _stats([entry.value1 for entry in b_entries])

    delta_avg = None
    delta_pct = None
    if a_stats and b_stats:
        delta_avg = round(b_stats["avg"] - a_stats["avg"], 2)
        if a_stats["avg"]:
            delta_pct = round((delta_avg / a_stats["avg"]) * 100, 2)

    return {
        "metric_type": metric_type,
        "a": {"from_day": a_from, "to_day": a_to, "days": len(a_entries), "stats": a_stats},
        "b": {"from_day": b_from, "to_day": b_to, "days": len(b_entries), "stats": b_stats},
        "delta_avg": delta_avg,
        "delta_pct": delta_pct,
    }
