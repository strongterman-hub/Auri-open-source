from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class PortraitCurrentResponse(BaseModel):
    presentation: str
    variant: str
    image_url: str
    image_url_small: str
    time_slot: str
    mood: str
    stage: str
    reason: str
    resolved_at: datetime
    expires_in_seconds: int
    enabled: bool
    weather_code: int | None = None
    temperature_c: float | None = None
    weather_kind: str | None = None
    season: str | None = None


class PortraitSettingsResponse(BaseModel):
    smart_background_enabled: bool = True
    available: bool = False


class PortraitSettingsRequest(BaseModel):
    smart_background_enabled: bool
