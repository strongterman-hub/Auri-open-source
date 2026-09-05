from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.agent.llm import LLMClient
from app.core.token_logger import token_context
from app.memory.models import (
    MemoryAction,
    MemoryOperation,
    MemoryScope,
    MemorySnapshot,
    MemoryTarget,
    OriginClass,
)
from app.observation.models import Observation
from app.observation.store import ObservationStore
from app.services.memory_service import MemoryService


CONSOLIDATION_SYSTEM_PROMPT = (
    "You are Auri's memory consolidator. Read the observations below and output "
    "a JSON array of short durable memory entries (plain strings) worth promoting "
    "into long-term memory. Keep entries compact and factual. Output only the JSON array."
)


@dataclass
class ConsolidationResult:
    promoted: int = 0
    candidates: list[str] = field(default_factory=list)
    error: str | None = None


class Consolidator:
    """Background pass that distills episodic observations into curated memory.

    Observations themselves are never written to the curated layer. Instead an
    agent-side distillation step proposes candidate entries, and a deterministic
    gate (dedupe / subsumption / budget) promotes only qualified ``agent``-origin
    entries. Untrusted and system observations therefore cannot reach curated
    memory directly.
    """

    def __init__(
        self,
        llm: LLMClient,
        memory_service: MemoryService,
        observation_store: ObservationStore,
        log_path: Path | None = None,
        max_candidates: int = 10,
        max_tokens: int = 2000,
    ) -> None:
        self.llm = llm
        self.memory_service = memory_service
        self.observation_store = observation_store
        self.log_path = Path(log_path) if log_path else None
        self.max_candidates = max_candidates
        self.max_tokens = max_tokens

    async def consolidate(self, scope: MemoryScope) -> ConsolidationResult:
        snapshot = await self.memory_service.snapshot(scope)
        observations = self.observation_store.query(
            scope.user_id, scope.agent_id, limit=200
        )
        candidates = await self._propose(observations, user_id=scope.user_id)
        operations = self._gate(candidates, snapshot)
        if not operations:
            self._log(scope, candidates, 0, None)
            return ConsolidationResult(candidates=candidates)

        result = await self.memory_service.write(scope, operations)
        if not result.success:
            self._log(scope, candidates, 0, result.error)
            return ConsolidationResult(candidates=candidates, error=result.error)

        self._log(scope, candidates, len(operations), None)
        return ConsolidationResult(promoted=len(operations), candidates=candidates)

    async def _propose(
        self,
        observations: list[Observation],
        *,
        user_id: str | None = None,
    ) -> list[str]:
        if not observations:
            return []
        text = "\n".join(
            f"- {observation.source.value}/{observation.kind} @ "
            f"{observation.observed_at.isoformat()}: "
            f"{json.dumps(observation.payload, ensure_ascii=False)}"
            for observation in observations
        )
        messages = [
            {"role": "system", "content": CONSOLIDATION_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        try:
            with token_context(kind="consolidation", user_id=user_id):
                response = await self.llm.complete(messages, tools=None, max_tokens=self.max_tokens)
        except Exception:
            return []
        candidates = self._parse_candidates(response.content)
        return candidates[: self.max_candidates]

    @staticmethod
    def _parse_candidates(content: str) -> list[str]:
        content = content.strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if not match:
                return []
            try:
                parsed = json.loads(match.group(0))
            except json.JSONDecodeError:
                return []
        if not isinstance(parsed, list):
            return []
        return [
            str(item).strip()
            for item in parsed
            if isinstance(item, str) and item.strip()
        ]

    def _gate(
        self,
        candidates: list[str],
        snapshot: MemorySnapshot,
    ) -> list[MemoryOperation]:
        existing = {entry.content.strip() for entry in snapshot.memory}
        existing.update(entry.content.strip() for entry in snapshot.user)
        operations: list[MemoryOperation] = []
        seen: set[str] = set()
        for candidate in candidates:
            candidate = candidate.strip()
            if not candidate or candidate in seen or candidate in existing:
                continue
            if any(candidate in entry or entry in candidate for entry in existing):
                continue
            operations.append(
                MemoryOperation(
                    action=MemoryAction.add,
                    target=MemoryTarget.memory,
                    content=candidate,
                    origin=OriginClass.agent,
                )
            )
            seen.add(candidate)
        return operations

    def _log(
        self,
        scope: MemoryScope,
        candidates: list[str],
        promoted: int,
        error: str | None,
    ) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "scope": scope.scope_key,
            "at": datetime.now(timezone.utc).isoformat(),
            "candidates": candidates,
            "promoted": promoted,
            "error": error,
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
