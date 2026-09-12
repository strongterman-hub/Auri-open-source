from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class TriggerType(str, Enum):
    time = "time"
    event = "event"


class ProactivePhase(str, Enum):
    onboarding = "onboarding"
    daily = "daily"


class ProactiveCategory(str, Enum):
    health_insight = "health_insight"
    health_care = "health_care"
    weather = "weather"
    profile_question = "profile_question"
    explore = "explore"
    goal_reminder = "goal_reminder"
    memory_recall = "memory_recall"
    trending = "trending"


class DeliveryChannel(str, Enum):
    chat = "chat"
    push = "push"
    none = "none"


class ConversationIntent(str, Enum):
    """How strongly a proactive message expects a conversational reply."""

    share = "share"
    soft_check_in = "soft_check_in"
    direct_question = "direct_question"


class EngagementState(str, Enum):
    planned = "planned"
    chat_persisted = "chat_persisted"
    exposed = "exposed"
    replied = "replied"
    seen_no_reply = "seen_no_reply"
    expired_unseen = "expired_unseen"
    no_reply_expected = "no_reply_expected"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProactiveDecision(BaseModel):
    """The outcome of one proactive evaluation for a user."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    agent_id: str = "default"
    session_id: str | None = None
    trigger_type: TriggerType
    trigger_source: str | None = None
    should_message: bool = False
    phase: ProactivePhase = ProactivePhase.daily
    category: ProactiveCategory | None = None
    insight_key: str | None = None
    topic_key: str | None = None
    message: str | None = None
    push_message: str | None = None
    importance: int | None = Field(default=None, ge=1, le=10)
    context_snapshot_id: str | None = None
    situation_summary: str | None = None
    situation_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    decision_reason: str | None = None
    silence_reason: str | None = None
    tool_calls: list[dict] = Field(default_factory=list)
    continuity_status: str | None = None
    continuity_reason: str | None = None
    continuity_refs: list[str] = Field(default_factory=list)
    actions: list[dict] = Field(default_factory=list)
    decided_at: datetime = Field(default_factory=_utcnow)
    conversation_intent: ConversationIntent = ConversationIntent.share
    delivery_channel: DeliveryChannel = DeliveryChannel.none
    chat_persisted_at: datetime | None = None
    push_status: str | None = None
    push_attempted_at: datetime | None = None
    acknowledged: bool = False
    exposed_at: datetime | None = None
    engagement_state: EngagementState = EngagementState.planned
    replied: bool = False
    replied_at: datetime | None = None
    settled_at: datetime | None = None
