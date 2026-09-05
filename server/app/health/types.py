"""Canonical health data type registry.

Single source of truth for metric/sample type names and their value semantics.
Keep these names aligned with the Android client enum values in
``com.auri.chat.HealthModels``.

Value slots (value1 / value2 / value3) are generic triple slots reused by the
SQLite-backed ``HealthStore``; the docstring below documents what each slot means
for a given type. Adding a new type is: register it here, map it in the Xiaomi
service, then add the matching client-side enum entry.
"""

from __future__ import annotations

# metric_type -> meaning of (value1, value2, value3)
METRIC_TYPES: dict[str, str] = {
    "STEPS": "value1=steps",
    "DISTANCE": "value1=meters",
    "CALORIES": "value1=kcal",
    "HEART_RATE": "value1=avg value2=min value3=max",
    "RESTING_HEART_RATE": "value1=avg value2=min value3=max",
    "SLEEP": "value1=duration_min value2=asleep_min value3=sleep_score",
    "WEIGHT": "value1=kg",
    "BODY_FAT": "value1=pct",
    "BMI": "value1=value",
    "MUSCLE_MASS": "value1=kg",
    "BODY_WATER": "value1=pct",
    "BONE_MASS": "value1=kg",
    "VISCERAL_FAT": "value1=score",
    "BMR": "value1=kcal",
    "SPO2": "value1=avg value2=min value3=max",
    "STRESS": "value1=avg value2=min value3=max",
}

# sample_type -> bucket semantics
SAMPLE_TYPES: dict[str, str] = {
    "HEART_RATE": "15min bucket value1=avg value2=min value3=max",
    "SLEEP_STAGE": "5min bucket value1=1deep 2light 3rem 4awake",
    "SLEEP_SESSION": "session value1=duration_min value2=asleep_min value3=vendor_score value4=main|nap",
    "RESTING_HEART_RATE": "point value1=bpm exact timestamp retained",
    "SLEEP_HRV": "sleep window value1=RMSSD_ms with source/quality metadata",
    "STEPS": "30min bucket value1=steps",
    "CALORIES": "60min bucket value1=kcal",
    "SPO2": "bucket value1=spo2_pct",
    "STRESS": "bucket value1=stress_score",
    "WORKOUT": "session value1=duration_min value2=kcal value3=avg_hr value4=activity_type",
    "ABNORMAL_HEART_BEAT": "event value1=duration_seconds",
}

ALL_METRIC_TYPES: frozenset[str] = frozenset(METRIC_TYPES)
ALL_SAMPLE_TYPES: frozenset[str] = frozenset(SAMPLE_TYPES)

# Types stored in the samples slot but shaped like sessions/events rather than
# uniform time buckets. They still fit the day -> bucket_start storage layout.
EVENT_TYPES: frozenset[str] = frozenset({"WORKOUT", "ABNORMAL_HEART_BEAT", "SLEEP_SESSION"})
