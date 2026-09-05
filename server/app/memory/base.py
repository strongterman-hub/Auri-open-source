from __future__ import annotations

from abc import ABC, abstractmethod

from app.memory.models import MemoryOperation, MemoryScope, MemorySnapshot, MemoryWriteResult


class MemoryStore(ABC):
    """Storage backend contract for durable memory."""

    @abstractmethod
    def load(self, scope: MemoryScope) -> MemorySnapshot:
        """Load a stable snapshot for the given scope."""

    @abstractmethod
    def write(
        self,
        scope: MemoryScope,
        operations: list[MemoryOperation],
    ) -> MemoryWriteResult:
        """Apply a batch of memory operations atomically."""

    @abstractmethod
    def delete_scope(self, scope: MemoryScope) -> None:
        """Delete all durable memory for the given scope."""
