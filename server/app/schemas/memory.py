from __future__ import annotations

from pydantic import BaseModel, Field

from app.memory.models import MemoryEntry, MemoryOperation, MemorySnapshot


class MemoryWriteRequest(BaseModel):
    operations: list[MemoryOperation] = Field(min_length=1, max_length=50)


class MemoryWriteResponse(BaseModel):
    success: bool
    error: str | None = None
    memory_usage: int = 0
    user_usage: int = 0
    memory: list[MemoryEntry] = Field(default_factory=list)
    user: list[MemoryEntry] = Field(default_factory=list)


class MemorySnapshotResponse(BaseModel):
    snapshot: MemorySnapshot


class MemoryConsolidationResponse(BaseModel):
    promoted: int
    candidates: list[str] = Field(default_factory=list)
    error: str | None = None
