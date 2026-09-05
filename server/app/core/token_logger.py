from __future__ import annotations

import json
import logging
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator


logger = logging.getLogger("auri.tokens")

_kind: ContextVar[str] = ContextVar("auri_token_kind", default="chat")
_session_id: ContextVar[str | None] = ContextVar("auri_token_session_id", default=None)
_user_id: ContextVar[str | None] = ContextVar("auri_token_user_id", default=None)


def current_token_user_id() -> str | None:
    return _user_id.get()


@contextmanager
def token_context(
    kind: str = "chat",
    session_id: str | None = None,
    user_id: str | None = None,
) -> Iterator[None]:
    """Attach caller context (kind/session/user) to token logs inside the block."""
    kind_token = _kind.set(kind)
    session_token = _session_id.set(session_id)
    user_token = _user_id.set(user_id)
    try:
        yield
    finally:
        _kind.reset(kind_token)
        _session_id.reset(session_token)
        _user_id.reset(user_token)


def usage_breakdown(usage: dict[str, Any] | None) -> dict[str, int]:
    """Normalize provider usage into input/output/cache-hit/cache-miss counters.

    Supports both the OpenAI shape (``prompt_tokens_details.cached_tokens``) and
    the DeepSeek shape (``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens``).
    """
    usage = usage or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0)

    details = usage.get("prompt_tokens_details") or {}
    cached = details.get("cached_tokens")
    if cached is None:
        cached = usage.get("prompt_cache_hit_tokens")
    cached = int(cached or 0)

    miss = usage.get("prompt_cache_miss_tokens")
    uncached = int(miss) if miss is not None else max(0, prompt - cached)

    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cached_tokens": cached,
        "uncached_tokens": uncached,
    }


class TokenLogger:
    """Append-only JSONL token-usage logger plus a human-readable log line."""

    def __init__(
        self,
        path: Path | None,
        usage_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.path = Path(path) if path else None
        self.usage_sink = usage_sink
        self._lock = threading.Lock()

    def log(
        self,
        *,
        model: str,
        usage: dict[str, Any] | None,
        kind: str | None = None,
        call_type: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        breakdown = usage_breakdown(usage)
        record: dict[str, Any] = {
            "id": uuid.uuid4().hex,
            "ts": datetime.now(timezone.utc).isoformat(),
            "model": model,
            "kind": kind or _kind.get(),
            "call_type": call_type,
            "session_id": session_id if session_id is not None else _session_id.get(),
            "user_id": user_id if user_id is not None else _user_id.get(),
            "duration_ms": round(duration_ms, 2) if duration_ms is not None else None,
            **breakdown,
        }

        if self.path is not None:
            try:
                with self._lock:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    with self.path.open("a", encoding="utf-8") as handle:
                        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            except OSError as exc:  # noqa: BLE001 - logging must never break a reply
                logger.warning("failed to write token usage log: %s", exc)

        logger.info(
            "tokens model=%s kind=%s session=%s user=%s prompt=%d completion=%d "
            "cached=%d uncached=%d total=%d",
            record["model"],
            record["kind"],
            record["session_id"],
            record["user_id"],
            record["prompt_tokens"],
            record["completion_tokens"],
            record["cached_tokens"],
            record["uncached_tokens"],
            record["total_tokens"],
        )
        if self.usage_sink is not None:
            self.usage_sink(record)
