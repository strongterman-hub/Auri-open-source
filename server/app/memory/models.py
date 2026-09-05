from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MemoryTarget(str, Enum):
    memory = "memory"
    user = "user"


class MemoryAction(str, Enum):
    add = "add"
    replace = "replace"
    remove = "remove"


class OriginClass(str, Enum):
    """Provenance of a durable memory entry.

    `origin` is set by server-side code (the memory tool runner or the
    consolidator), never deserialized from model prose, so the agent cannot
    rewrite its own trust classification.
    """

    owner = "owner"
    agent = "agent"
    untrusted = "untrusted"
    system = "system"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MemoryEntry(BaseModel):
    """A single curated-memory entry with provenance and lineage metadata."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=lambda: uuid4().hex)
    content: str
    origin: OriginClass = OriginClass.agent
    observed_at: datetime = Field(default_factory=_utcnow)
    supersession_key: str | None = None
    importance: int | None = Field(default=None, ge=1, le=10)
    trigger: list[str] | None = None
    source_ref: str | None = None


class MemoryOperation(BaseModel):
    """A single add/replace/remove operation against one memory target."""

    action: MemoryAction
    target: MemoryTarget = MemoryTarget.memory
    content: str | None = None
    old_text: str | None = None
    origin: OriginClass = OriginClass.agent
    importance: int | None = Field(default=None, ge=1, le=10)
    trigger: list[str] | None = None
    source_ref: str | None = None

    @model_validator(mode="after")
    def _validate_operation(self) -> "MemoryOperation":
        if self.action is MemoryAction.add:
            if not self.content or not self.content.strip():
                raise ValueError("'content' is required for add operations.")
        if self.action is MemoryAction.replace:
            if not self.content or not self.content.strip():
                raise ValueError("'content' is required for replace operations.")
            if not self.old_text or not self.old_text.strip():
                raise ValueError("'old_text' is required for replace operations.")
        if self.action is MemoryAction.remove:
            if not self.old_text or not self.old_text.strip():
                raise ValueError("'old_text' is required for remove operations.")
        return self


class MemoryScope(BaseModel):
    """The isolation boundary for a memory store."""

    model_config = ConfigDict(frozen=True)

    user_id: str = Field(min_length=1)
    agent_id: str = "default"

    @property
    def scope_key(self) -> str:
        return f"{self.agent_id}:{self.user_id}"


class MemorySnapshot(BaseModel):
    """A stable view of memory suitable for injecting into a prompt."""

    memory: list[MemoryEntry] = Field(default_factory=list)
    user: list[MemoryEntry] = Field(default_factory=list)
    memory_usage: int = 0
    user_usage: int = 0


class MemoryWriteResult(BaseModel):
    success: bool
    error: str | None = None
    target: MemoryTarget | None = None
    entries: list[MemoryEntry] = Field(default_factory=list)
    memory_usage: int = 0
    user_usage: int = 0
