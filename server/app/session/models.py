from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field


class SessionCreate(BaseModel):
    user_id: str = Field(min_length=1)
    agent_id: str = "default"
    metadata: dict = Field(default_factory=dict)


class Session(BaseModel):
    id: str
    user_id: str
    agent_id: str = "default"
    messages: list[dict] = Field(default_factory=list)
    # Sidecar rolling summary. `summary_cursor` is the index of the first
    # message NOT yet covered by `summary`; messages before it are compacted
    # and no longer sent verbatim, while `messages` itself stays append-only.
    summary: str | None = None
    summary_cursor: int = 0
    summary_generation: int = 0
    # Session lineage for compression/reset rotation.
    parent_session_id: str | None = None
    end_reason: str | None = None
    # One-shot notice shown after an automatic reset (idle/daily).
    reset_notice: str | None = None
    # Last real prompt token count reported by the model, used to refine the
    # next compression decision.
    last_prompt_tokens: int = 0
    metadata: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(cls, payload: SessionCreate, session_id: str, now: datetime | None = None) -> "Session":
        current = now or datetime.now(timezone.utc)
        return cls(
            id=session_id,
            user_id=payload.user_id,
            agent_id=payload.agent_id,
            metadata=payload.metadata,
            created_at=current,
            updated_at=current,
        )

    def touch(self, now: datetime | None = None) -> None:
        self.updated_at = now or datetime.now(timezone.utc)

    def tail_messages(self) -> list[dict]:
        """Return only the messages not yet absorbed into the rolling summary."""
        return self.messages[self.summary_cursor :]

    def mark_compacted(self, summary: str, cursor: int, generation: int) -> None:
        """Record a compaction result without mutating the full transcript."""
        self.summary = summary
        self.summary_cursor = cursor
        self.summary_generation = generation

    def normalize_message_ids(self) -> bool:
        """Backfill stable ids/timestamps for messages written before this schema.

        Pagination uses ``id`` as its cursor, so legacy messages that only carry
        ``role``/``content`` need deterministic ids and monotonically increasing
        timestamps. Returns ``True`` when anything changed (and should be saved).
        """
        changed = False
        base = self.created_at
        for index, message in enumerate(self.messages):
            if not message.get("id"):
                message["id"] = f"legacy-{index}"
                changed = True
            if not message.get("timestamp"):
                message["timestamp"] = (base + timedelta(seconds=index)).isoformat()
                changed = True
        return changed
