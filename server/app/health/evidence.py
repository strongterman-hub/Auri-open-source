"""Semantic labels for model-facing health data; never infer naps from a delta."""

from datetime import datetime

HEALTH_MEANING = {
    "SLEEP": {
        "value1": "daily_total_sleep_minutes_may_include_multiple_episodes",
        "value2": "daily_asleep_minutes",
        "value3": "vendor_sleep_score",
        "rule": "Daily total is NOT last night main sleep. Do not infer nap duration from a difference.",
    },
    "WORKOUT": {
        "bucket_start": "activity_occurred_start",
        "bucket_end": "activity_occurred_end",
        "rule": "Sync time is not activity time. Route, place and cause are unknown unless independently confirmed.",
    },
    "rule": "Re-syncing an unchanged measurement is not a new user event or a reason to repeat the same observation.",
}


def sleep_evidence(metrics, scores, samples=()):
    by_day = {s.sleep_day: s for s in scores}
    result = []
    for m in metrics:
        if m.metric_type != "SLEEP":
            continue
        score = by_day.get(m.day)
        main = None
        if score:
            start = datetime.fromisoformat(score.session_start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(score.session_end.replace("Z", "+00:00"))
            main = {
                "start": score.session_start,
                "end": score.session_end,
                "interval_minutes": round((end - start).total_seconds() / 60, 1),
                "rule": "Episode interval may include awake time; not interchangeable with asleep minutes.",
            }
        result.append(
            {
                "day": m.day,
                "daily_total_sleep_minutes": m.value1,
                "main_sleep_episode": main,
                "nap_minutes": (
                    sum(
                        float(s.value1)
                        for s in samples
                        if s.metric_type == "SLEEP_SESSION"
                        and s.day == m.day
                        and s.value4 == "nap"
                        and s.value1 is not None
                    )
                    if any(
                        s.metric_type == "SLEEP_SESSION"
                        and s.day == m.day
                        and s.value4 == "nap"
                        for s in samples
                    )
                    else None
                ),
                "completeness": "not_guaranteed",
                "record_updated_at": m.updated_at,
                "rule": HEALTH_MEANING["SLEEP"]["rule"],
            }
        )
    return result
