from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthMetricIn(BaseModel):
    metric_type: str
    day: str
    day_start: str | None = None
    value1: float | None = None
    value2: float | None = None
    value3: float | None = None
    source: str | None = None
    source_updated_at: int = 0
    resolution_policy: str | None = None
    updated_at: int = 0


class HealthSyncRequest(BaseModel):
    from_day: str
    to_day: str
    timezone: str | None = None
    metric_types: list[str] = Field(default_factory=list)
    metrics: list[HealthMetricIn] = Field(default_factory=list)
    samples: list[HealthSampleIn] = Field(default_factory=list)


class HealthMetricOut(HealthMetricIn):
    pass


class HealthSampleIn(BaseModel):
    metric_type: str
    day: str
    bucket_start: str
    bucket_end: str
    value1: float | None = None
    value2: float | None = None
    value3: float | None = None
    value4: str | None = None
    source: str | None = None
    quality: float | None = None
    metadata: dict[str, Any] | None = None
    updated_at: int = 0


class HealthSampleOut(HealthSampleIn):
    pass


class HealthSyncResponse(BaseModel):
    synced: int


class HealthMetricsResponse(BaseModel):
    metrics: list[HealthMetricOut]
    samples: list[HealthSampleOut] = Field(default_factory=list)


class SleepScoreComponent(BaseModel):
    score: int
    weight: float
    effective_weight: float
    quality: float
    value: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class SleepScoreDimension(BaseModel):
    score: int | None = None
    confidence: int = 0
    status: str
    components: dict[str, SleepScoreComponent] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)


class SleepCalibrationOut(BaseModel):
    state: str
    day: int | None = None
    total_days: int = 14
    valid_nights: int = 0


class SleepScoreMeta(BaseModel):
    algorithm_version: str
    vendor_score: int | None = None
    computed_at: int


class SleepScoreOut(BaseModel):
    sleep_day: str
    session_start: str
    session_end: str
    sleep_health: SleepScoreDimension
    recovery: SleepScoreDimension
    calibration: SleepCalibrationOut
    meta: SleepScoreMeta


class SleepScoresResponse(BaseModel):
    visible: bool
    scores: list[SleepScoreOut] = Field(default_factory=list)
