from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PersonaPresetsResponse(BaseModel):
    presets: list[dict[str, Any]] = Field(default_factory=list)


class PersonaSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preset_id: str | None = Field(default=None, min_length=1, max_length=64)
    presentation: Literal["female", "male"] | None = None

    @model_validator(mode="after")
    def _require_one(self) -> PersonaSelectionRequest:
        if self.preset_id is None and self.presentation is None:
            raise ValueError("preset_id or presentation is required")
        return self


class PersonaSelectionSummary(BaseModel):
    preset_id: str
    presentation: Literal["female", "male"] = "female"
    source: Literal["user", "default"] = "default"
class PersonaPresetSummary(BaseModel):
    id: str
    label: str = ""
    description: str = ""
    proactive_frequency_preset: str = "high"


class PersonaCharacterSummary(BaseModel):
    presentation: Literal["female", "male"]
    label: str
    display_name: str = "Auri"
    summary: str = ""
    avatar_url: str = ""


def _default_selection() -> PersonaSelectionSummary:
    return PersonaSelectionSummary(preset_id="warm_friend")


class PersonaMeResponse(BaseModel):
    preset: dict[str, Any] = Field(default_factory=dict)
    relationship: dict[str, Any] = Field(default_factory=dict)
    user_selection_enabled: bool = False
    available: bool = False
    portrait_available: bool = False
    selection: PersonaSelectionSummary = Field(default_factory=_default_selection)
    presets: list[PersonaPresetSummary] = Field(default_factory=list)
    characters: list[PersonaCharacterSummary] = Field(default_factory=list)


class PersonaSelectionResponse(BaseModel):
    selection: PersonaSelectionSummary
    updated_at: datetime