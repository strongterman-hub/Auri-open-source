from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.agent.tools import MemorySearchTool
from app.memory.file_store import FileMemoryStore
from app.memory.models import MemoryAction, MemoryOperation, MemoryScope, MemoryTarget
from app.observation.models import Observation, ObservationSource
from app.observation.store import ObservationStore
from app.services.memory_service import MemoryService
from app.services.observation_service import ObservationService


def test_memory_search_returns_observations_and_curated(tmp_dir: Path) -> None:
    memory_store = FileMemoryStore(tmp_dir / "mem")
    scope = MemoryScope(user_id="u1")
    memory_store.write(
        scope,
        [
            MemoryOperation(
                action=MemoryAction.add,
                target=MemoryTarget.memory,
                content="Prefers dark mode",
            )
        ],
    )
    observation_store = ObservationStore(tmp_dir / "memory.db")
    observation_store.add(
        Observation(
            user_id="u1",
            source=ObservationSource.health,
            kind="SLEEP",
            payload={"day": "2026-08-19", "value1": 420.0},
        )
    )
    tool = MemorySearchTool(
        observation_service=ObservationService(observation_store),
        memory_service=MemoryService(memory_store),
        scope=scope,
    )

    output = json.loads(asyncio.run(tool.execute(query="sleep", source="health")))

    assert len(output["observations"]) == 1
    assert output["observations"][0]["kind"] == "SLEEP"
    assert "untrusted" in output["note"]
