from __future__ import annotations

import json
import re
from datetime import datetime

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
    ) -> list[EventCandidate]:
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
        messages = [
            {"role": "system", "content": EVENT_EXTRACTION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"MESSAGE_TIME: {message_at.isoformat()}\n"
                    f"USER_TIMEZONE: {timezone_name}\n"
                    f"RECENT_EVENTS: {json.dumps(recent, ensure_ascii=False)}\n\n"
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
            return []
        return self.parse(response.content)

    @staticmethod
    def parse(content: str) -> list[EventCandidate]:
        text = (content or "").strip()
        if not text:
            return []
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if not match:
                return []
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                return []
        if not isinstance(payload, list):
            return []
        candidates: list[EventCandidate] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                candidates.append(EventCandidate.model_validate(item))
            except Exception:
                continue
        return candidates
