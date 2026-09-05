from __future__ import annotations

import asyncio
from pathlib import Path

from app.agent.llm import LLMClient, LLMResponse
from app.memory.consolidation import Consolidator
from app.memory.file_store import FileMemoryStore
from app.memory.models import MemoryAction, MemoryOperation, MemoryScope, MemoryTarget
from app.observation.models import Observation, ObservationSource
from app.observation.store import ObservationStore
from app.services.memory_service import MemoryService


class FakeLLM(LLMClient):
    def __init__(self, content: str) -> None:
        self.content = content

    async def complete(self, messages, tools=None, max_tokens=None):
        return LLMResponse(content=self.content)

    async def stream(self, messages, tools=None, max_tokens=None):
        raise NotImplementedError


def test_consolidate_promotes_agent_derived_candidates(tmp_dir: Path) -> None:
    memory_store = FileMemoryStore(tmp_dir / "mem")
    observation_store = ObservationStore(tmp_dir / "memory.db")
    observation_store.add(
        Observation(
            user_id="u1",
            source=ObservationSource.health,
            kind="STEPS",
            payload={"day": "2026-08-19", "value1": 1000.0},
        )
    )
    consolidator = Consolidator(
        FakeLLM('["User averaged 1000 steps recently"]'),
        MemoryService(memory_store),
        observation_store,
        log_path=tmp_dir / "consolidation_log.jsonl",
    )

    result = asyncio.run(consolidator.consolidate(MemoryScope(user_id="u1")))

    assert result.promoted == 1
    snapshot = memory_store.load(MemoryScope(user_id="u1"))
    assert [entry.content for entry in snapshot.memory] == ["User averaged 1000 steps recently"]
    assert snapshot.memory[0].origin.value == "agent"


def test_consolidate_drops_duplicate_candidates(tmp_dir: Path) -> None:
    memory_store = FileMemoryStore(tmp_dir / "mem")
    scope = MemoryScope(user_id="u1")
    memory_store.write(
        scope,
        [
            MemoryOperation(
                action=MemoryAction.add,
                target=MemoryTarget.memory,
                content="User averaged 1000 steps recently",
            )
        ],
    )
    consolidator = Consolidator(
        FakeLLM('["User averaged 1000 steps recently"]'),
        MemoryService(memory_store),
        ObservationStore(tmp_dir / "memory.db"),
    )

    result = asyncio.run(consolidator.consolidate(scope))

    assert result.promoted == 0
    assert len(memory_store.load(scope).memory) == 1
