"""Bounded auxiliary JSON calls; incomplete answers are never valid empty results."""

from __future__ import annotations

import json
import re


async def structured_json(
    llm, messages, *, max_tokens=4096, expected=dict, validate=None
):
    last_error = ValueError("No structured response")
    for attempt in range(2):
        try:
            response = await llm.complete_structured(messages, max_tokens=max_tokens)
            if response.finish_reason == "length" or not response.content.strip():
                raise ValueError("truncated_or_empty")
            text = response.content.strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
            value = json.loads(text)
            if not isinstance(value, expected) or (validate and not validate(value)):
                raise ValueError("invalid_schema")
            return value
        except (ValueError, TypeError) as exc:
            last_error = exc
            messages = [
                *messages,
                {
                    "role": "user",
                    "content": "Return only a complete compact JSON value matching the required schema. No explanation.",
                },
            ]
        except Exception:
            # Transport/credit failures are handled by the durable task retry,
            # not amplified into an immediate extra paid call.
            raise
    raise last_error
