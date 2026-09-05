from __future__ import annotations

import asyncio

from app.memory.base import MemoryStore
from app.memory.models import MemoryOperation, MemoryScope, MemorySnapshot, MemoryWriteResult


class MemoryService:
    """Reads and writes durable memory through the configured backend."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store

    async def snapshot(self, scope: MemoryScope) -> MemorySnapshot:
        return await asyncio.to_thread(self.store.load, scope)

    async def write(
        self,
        scope: MemoryScope,
        operations: list[MemoryOperation],
    ) -> MemoryWriteResult:
        return await asyncio.to_thread(self.store.write, scope, operations)

    async def delete(self, scope: MemoryScope) -> None:
        await asyncio.to_thread(self.store.delete_scope, scope)

    @staticmethod
    def build_system_prompt(snapshot: MemorySnapshot) -> str:
        blocks: list[str] = []
        if snapshot.memory:
            blocks.append(
                "MEMORY (your durable notes about the user's environment, projects, and decisions):\n"
                + "\n".join(f"- {entry.content}" for entry in snapshot.memory)
            )
        if snapshot.user:
            blocks.append(
                "USER PROFILE (who the user is, preferences, and communication style):\n"
                + "\n".join(f"- {entry.content}" for entry in snapshot.user)
            )
        return "\n\n".join(blocks) if blocks else "No durable memory is available yet."
