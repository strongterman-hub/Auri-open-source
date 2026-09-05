from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DailyActivity(BaseModel):
    """A single day of activity totals."""

    date: str
    steps: int = 0
    distance_m: float = 0.0
    active_kcal: float = 0.0
    steps_source: str | None = None
    distance_source: str | None = None
    calories_source: str | None = None
    steps_source_updated_at: int = 0
    distance_source_updated_at: int = 0
    calories_source_updated_at: int = 0


class SleepStage(BaseModel):
    """One sleep stage, with timestamps retained for Auri bucketing."""

    stage: Literal["deep", "light", "rem", "awake"]
    start_time: datetime | None = None
    end_time: datetime | None = None
    minutes: int = 0


class SleepSession(BaseModel):
    """A full sleep session."""

    start_at: datetime
    end_at: datetime
    duration_minutes: int = 0
    time_asleep_minutes: int = 0
    time_awake_minutes: int = 0
    sleep_score: int | None = None
    sleep_hrv_rmssd_ms: float | None = None
    sleep_hrv_source: str | None = None
    is_nap: bool = False
    stages: list[SleepStage] = Field(default_factory=list)


class HeartRateSample(BaseModel):
    """A single heart-rate measurement."""

    timestamp: datetime
    bpm: int
    sample_type: Literal["resting", "active", "passive", "workout"] = "passive"


class BodyMeasurement(BaseModel):
    """A body-composition measurement from a smart scale."""

    timestamp: datetime
    weight_kg: float
    bmi: float | None = None
    body_fat_pct: float | None = None
    muscle_mass_kg: float | None = None
    water_pct: float | None = None
    bone_mass_kg: float | None = None
    visceral_fat_score: int | None = None
    basal_metabolism_kcal: int | None = None
    metabolic_age: int | None = None


class SpO2Sample(BaseModel):
    """A blood-oxygen measurement."""

    timestamp: datetime
    spo2_pct: int


class StressSample(BaseModel):
    """A stress measurement."""

    timestamp: datetime
    stress_score: int
    level: Literal["low", "medium", "high"]


class Workout(BaseModel):
    """A workout/sport session."""

    start_at: datetime
    end_at: datetime
    duration_minutes: int = 0
    activity_type: str = "workout"
    distance_m: float | None = None
    calories_kcal: float | None = None
    avg_heart_rate_bpm: int | None = None
    max_heart_rate_bpm: int | None = None
    avg_pace_sec_per_km: float | None = None
    max_pace_sec_per_km: float | None = None
    total_steps: int | None = None


class AbnormalHeartBeatEvent(BaseModel):
    """An abnormal heart-beat event."""

    start_at: datetime
    end_at: datetime
    duration_seconds: int = 0
