from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


PRESENTATIONS: tuple[str, ...] = ("female", "male")
STYLE_ORDER: tuple[str, ...] = ("micro", "short", "normal", "detailed")
STYLE_RANK = {name: index for index, name in enumerate(STYLE_ORDER)}
FREQUENCY_PRESETS: tuple[str, ...] = ("quiet", "normal", "high", "intensive")
FREQUENCY_DAILY_LIMITS: dict[str, int] = {
    "quiet": 4,
    "normal": 12,
    "high": 24,
    "intensive": 40,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_style(value: str | None, *, fallback: str = "short") -> str:
    text = str(value or "").strip().lower()
    return text if text in STYLE_RANK else fallback


def cap_style(planned: str, maximum: str) -> str:
    planned_norm = normalize_style(planned)
    maximum_norm = normalize_style(maximum)
    if STYLE_RANK[planned_norm] <= STYLE_RANK[maximum_norm]:
        return planned_norm
    return maximum_norm


class PersonaPreset(BaseModel):
    """A configurable Auri character preset."""

    id: str = "warm_friend"
    version: str = "v1"
    name: str = "Auri"
    label: str = ""
    description: str = ""
    identity: str = ""
    temperament: list[str] = Field(default_factory=list)
    speech_style: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    banned_phrases: list[str] = Field(default_factory=list)
    emoji_level: Literal["off", "low", "medium"] = "low"
    question_level: Literal["off", "low", "normal"] = "low"
    health_advice_level: Literal["off", "low", "normal"] = "low"
    proactive_frequency_preset: Literal["quiet", "normal", "high", "intensive"] = "high"
    allow_disagreement: bool = True
    allow_uncertainty: bool = True
    prompt_override: str | None = None


class PersonaOverrides(BaseModel):
    identity: str | None = None
    temperament: list[str] | None = None
    speech_style: list[str] | None = None
    interests: list[str] | None = None
    boundaries: list[str] | None = None
    banned_phrases: list[str] | None = None
    emoji_level: Literal["off", "low", "medium"] | None = None
    question_level: Literal["off", "low", "normal"] | None = None
    health_advice_level: Literal["off", "low", "normal"] | None = None
    proactive_frequency_preset: (
        Literal["quiet", "normal", "high", "intensive"] | None
    ) = None
    allow_disagreement: bool | None = None
    allow_uncertainty: bool | None = None
    prompt_override: str | None = None
    presentation: Literal["female", "male"] | None = None


class CharacterCard(BaseModel):
    """A presentation-level character card (female / male Auri).

    The card never changes the brand name or the preset's core temperament;
    it only adds a small, explicit layer used by the prompt and portrait
    variant resolver.
    """

    presentation: Literal["female", "male"] = "female"
    version: str = "v1"
    display_name: str = "Auri"
    label: str = "女"
    summary: str = ""
    appearance: str = ""
    speech_quirks: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    emoji_offset: int = 0
    address_hint: str = ""


class UserPersonaSelection(BaseModel):
    preset_id: str
    overrides: PersonaOverrides = Field(default_factory=PersonaOverrides)
    selected_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class RelationshipState(BaseModel):
    stage: Literal["new", "warming", "familiar", "close"] = "warming"
    address: str | None = None
    tone: Literal["casual", "gentle", "quiet", "playful"] = "casual"
    shared_topics: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    last_correction_at: datetime | None = None
    recent_correction_count: int = 0
    last_user_tone: str | None = None
    updated_at: datetime = Field(default_factory=_utcnow)


class OpenLoop(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    kind: Literal["question", "commitment"] = "question"
    topic_key: str | None = None
    summary: str = ""
    source: Literal["chat", "proactive"] = "chat"
    source_ref: str | None = None
    asked_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime
    status: Literal["open", "answered", "dropped", "expired"] = "open"
    closed_at: datetime | None = None


class AgentNote(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    kind: Literal["commitment", "preference_used"] = "commitment"
    summary: str = ""
    source_ref: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime | None = None


class PortraitState(BaseModel):
    """One resolved smart-background state for a user."""

    variant: str = "day_gentle"
    presentation: str = "female"
    time_slot: str = "day"
    mood: str = "neutral"
    stage: str = "warming"
    reason: str = ""
    mood_hint: str | None = None
    mood_hint_at: datetime | None = None
    signals: dict[str, Any] = Field(default_factory=dict)
    resolved_at: datetime = Field(default_factory=_utcnow)


class PortraitSettings(BaseModel):
    """Per-user smart-background preference."""

    smart_background_enabled: bool = True
    updated_at: datetime = Field(default_factory=_utcnow)


class BehaviorPolicy(BaseModel):
    allow_question: bool = True
    max_style: str = "short"
    allow_health_advice: bool = True
    allow_list: bool = False
    allow_silent: bool = False
    prefer_silent: bool = False
    lane_hint: Literal["fast", "normal", "away"] | None = None
    reason: str = ""
    persona_id: str = "warm_friend"
    frequency_preset: str = "high"
    open_loop_count: int = 0
    is_task: bool = False
    is_safety: bool = False
    is_question: bool = False
    is_low_information: bool = False


class ProactiveContext(BaseModel):
    persona_prompt: str = ""
    relationship_prompt: str = ""
    behavior_prompt: str = ""
    frequency_preset: str = "high"
    daily_limit: int = 24
    open_loop_count: int = 0