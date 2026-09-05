from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class ReminderKind(str, Enum):
    time = "time"
    event = "event"


class ReminderStatus(str, Enum):
    pending = "pending"
    done = "done"
    cancelled = "cancelled"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Reminder(BaseModel):
    """A user-visible reminder scheduled by the agent.

    ``kind=time`` uses ``due_at`` for a one-shot reminder. ``kind=event`` uses
    ``metric_type`` / ``operator`` / ``threshold`` to re-evaluate a daily health
    condition and fires at most once per day.
    """

    id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str
    agent_id: str = "default"
    kind: ReminderKind
    message: str
    due_at: datetime | None = None
    metric_type: str | None = None
    operator: str | None = None
    threshold: float | None = None
    status: ReminderStatus = ReminderStatus.pending
    last_fired_at: datetime | None = None
    created_at: datetime = Field(default_factory=_utcnow)
