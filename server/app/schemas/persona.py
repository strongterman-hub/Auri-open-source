from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PersonaPresetsResponse(BaseModel):
    presets: list[dict[str, Any]] = Field(default_factory=list)


class PersonaSelectionRequest(BaseModel):
    preset_id: str = Field(min_length=1, max_length=64)
    overrides: dict[str, Any] = Field(default_factory=dict)


class PersonaSelectionResponse(BaseModel):
    preset_id: str
    overrides: dict[str, Any] = Field(default_factory=dict)
    selected_at: datetime
    updated_at: datetime


class PersonaMeResponse(BaseModel):
    preset: dict[str, Any] = Field(default_factory=dict)
    relationship: dict[str, Any] = Field(default_factory=dict)
    user_selection_enabled: bool = False