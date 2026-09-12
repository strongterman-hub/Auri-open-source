from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.agent.llm import LLMClient
from app.core.token_logger import token_context
from app.memory.events import EventCandidate, EventRecord


EVENT_EXTRACTION_SYSTEM_PROMPT = """You extract the user's real-life events into a timeline.
Return only a JSON array. Return [] when the message contains no event, plan, state change,
correction, or meaningful completed action.

Rules:
- Treat only facts asserted by the USER as owner facts. Never turn assistant guesses into facts.
- Resolve relative time from MESSAGE_TIME in USER_TIMEZONE, never from processing time.
- Reuse an existing event_key/thread_key when the message updates the same event.
- CONVERSATION_CONTEXT is supplied only to resolve what the current user message
  answers or refers to. It is not owner evidence by itself. Only facts asserted
  or confirmed by the current USER_MESSAGE may create or update owner facts.
- Short replies such as "already done", "no difference", or a bare product/game
  name should update the event discussed by the immediately preceding question.
- status must be planned, in_progress, completed, or cancelled.
- planned events must not be represented as completed.
- occurred_start/occurred_end must be ISO-8601 timestamps with an offset when known, else null.
- current_until is optional and should bound short-lived current states such as
  travelling or eating.
- Keep location null unless the user explicitly states it or the existing event
  already establishes it.
- A correction may update the same event_key or set corrects_event_key to a conflicting event.
- details must contain only small factual details worth remembering. Use protected=true only for an
  explicit correction, user request to remember, or safety-critical detail.
- event_key and thread_key are short stable snake_case identifiers. Include a date/month suffix when
  the concept can recur.

Each array item has this shape:
{
  "event_key": "...",
  "thread_key": "... or null",
  "kind": "...",
  "title": "...",
  "core_summary": "...",
  "status": "planned|in_progress|completed|cancelled",
  "occurred_start": "ISO-8601 or null",
  "occurred_end": "ISO-8601 or null",
  "time_precision": "minute|hour|day|week|month|unknown",
  "timezone": "IANA timezone or null",
  "location": "... or null",
  "current_until": "ISO-8601 or null",
  "importance": 0.0,
  "confidence": 0.0,
  "details": [
    {"detail_key":"...","content":"...","salience":0.0,"confidence":0.0,"protected":false}
  ],
  "relations": [
    {"target_event_key":"...","relation":"before|after|during|caused_by|follows|corrects"}
  ],
  "corrects_event_key": "... or null"
}
"""


@dataclass(frozen=True)
class EventExtractionOutcome:
    candidates: list[EventCandidate]
    status: str


class EventExtractor:
    """LLM-backed extractor constrained to one timestamped owner message."""

    def __init__(self, llm: LLMClient, max_tokens: int = 4096) -> None:
        self.llm = llm
        self.max_tokens = max_tokens

    async def extract(
        self,
        *,
        user_id: str,
        message_text: str,
        message_at: datetime,
        timezone_name: str,
        recent_events: list[EventRecord],
        conversation_context: list[dict[str, Any]] | None = None,
    ) -> list[EventCandidate]:
        outcome = await self.extract_with_diagnostics(
            user_id=user_id,
            message_text=message_text,
            message_at=message_at,
            timezone_name=timezone_name,
            recent_events=recent_events,
            conversation_context=conversation_context,
        )
        return outcome.candidates

    async def extract_with_diagnostics(
        self,
        *,
        user_id: str,
        message_text: str,
        message_at: datetime,
        timezone_name: str,
        recent_events: list[EventRecord],
        conversation_context: list[dict[str, Any]] | None = None,
    ) -> EventExtractionOutcome:
        recent = [
            {
                "event_key": event.event_key,
                "thread_key": event.thread_key,
                "kind": event.kind,
                "title": event.title,
                "core_summary": event.core_summary,
                "status": event.status.value,
                "occurred_start": (
                    event.occurred_start.isoformat() if event.occurred_start else None
                ),
                "occurred_end": event.occurred_end.isoformat() if event.occurred_end else None,
                "location": event.location,
            }
            for event in recent_events[:20]
        ]
        context = []
        for message in (conversation_context or [])[-4:]:
            role = str(message.get("role") or "unknown")
            content = message.get("content")
            if isinstance(content, list):
                content = "\n".join(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                )
            context.append(
                {
                    "id": message.get("id"),
                    "role": role,
                    "timestamp": message.get("timestamp"),
                    "text": str(content or "")[:1000],
                }
            )
        messages = [
            {"role": "system", "content": EVENT_EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"MESSAGE_TIME: {message_at.isoformat()}\n"
                    f"USER_TIMEZONE: {timezone_name}\n"
                    f"RECENT_EVENTS: {json.dumps(recent, ensure_ascii=False)}\n\n"
                    f"CONVERSATION_CONTEXT: {json.dumps(context, ensure_ascii=False)}\n\n"
                    f"USER_MESSAGE:\n{message_text}"
                ),
            },
        ]
        try:
            with token_context(kind="event_extraction", user_id=user_id):
                response = await self.llm.complete(
                    messages,
                    tools=None,
                    max_tokens=self.max_tokens,
                )
        except Exception:
            return EventExtractionOutcome([], "extractor_error")
        return self.parse_with_diagnostics(response.content)

    @staticmethod
    def parse(content: str) -> list[EventCandidate]:
        return EventExtractor.parse_with_diagnostics(content).candidates

    @staticmethod
    def parse_with_diagnostics(content: str) -> EventExtractionOutcome:
        text = (content or "").strip()
        if not text:
            return EventExtractionOutcome([], "empty")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if not match:
                return EventExtractionOutcome([], "invalid_json")
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                return EventExtractionOutcome([], "invalid_json")
        if not isinstance(payload, list):
            return EventExtractionOutcome([], "invalid_shape")
        candidates: list[EventCandidate] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                candidates.append(EventCandidate.model_validate(item))
            except Exception:
                continue
        if candidates:
            return EventExtractionOutcome(candidates, "ok")
        return EventExtractionOutcome(
            [],
            "no_candidates" if not payload else "validation_empty",
        )
