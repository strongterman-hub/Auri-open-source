from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.memory.models import OriginClass


class ObservationSource(str, Enum):
    health = "health"
    schedule = "schedule"
    phone_state = "phone_state"
    weather = "weather"
    conversation = "conversation"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Observation(BaseModel):
    """A single timestamped, provenance-bearing event from an external source."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    agent_id: str = "default"
    source: ObservationSource
    observed_at: datetime = Field(default_factory=_utcnow)
    origin: OriginClass = OriginClass.untrusted
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    supersession_key: str | None = None
