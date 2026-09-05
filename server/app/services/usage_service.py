from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.pricing import normalize_model, usage_cost


def read_usage_log(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    path = Path(path)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def read_turn_log(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    path = Path(path)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def build_turns_report(
    path: Path | None,
    *,
    days: int = 30,
    limit: int = 100,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Read the turn log and return recent turns plus tool-call/error aggregates."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    turns: list[dict[str, Any]] = []
    for record in read_turn_log(path):
        raw_ts = record.get("started_at")
        try:
            dt = datetime.fromisoformat(raw_ts)
        except (TypeError, ValueError):
            continue
        if dt < cutoff:
            continue
        if user_id is not None and record.get("user_id") != user_id:
            continue

        tool_calls = record.get("tool_calls") or []
        cached = int(record.get("cached_tokens") or 0)
        uncached = int(record.get("uncached_tokens") or 0)
        completion = int(record.get("completion_tokens") or 0)
        model = normalize_model(record.get("model"))
        cost = usage_cost(model, raw_ts, cached, uncached, completion)

        turns.append(
            {
                "started_at": raw_ts,
                "duration_ms": record.get("duration_ms"),
                "session_id": record.get("session_id"),
                "user_id": record.get("user_id"),
                "model": model,
                "llm_calls": int(record.get("llm_calls") or 0),
                "tool_calls": [
                    {
                        "name": tc.get("name"),
                        "duration_ms": tc.get("duration_ms"),
                        "error": tc.get("error"),
                        "result_chars": tc.get("result_chars"),
                    }
                    for tc in tool_calls
                ],
                "prompt_tokens": int(record.get("prompt_tokens") or 0),
                "completion_tokens": completion,
                "cached_tokens": cached,
                "uncached_tokens": uncached,
                "status": record.get("status"),
                "response_chars": int(record.get("response_chars") or 0),
                "cost": cost,
            }
        )

    turns.sort(key=lambda item: item["started_at"], reverse=True)

    tool_counts: dict[str, int] = defaultdict(int)
    error_count = 0
    total_duration_ms = 0.0
    for turn in turns:
        total_duration_ms += float(turn["duration_ms"] or 0)
        for tool_call in turn["tool_calls"]:
            tool_counts[tool_call["name"] or "unknown"] += 1
            if tool_call["error"]:
                error_count += 1

    return {
        "generated_at": now.isoformat(),
        "days": days,
        "filter_user_id": user_id,
        "totals": {
            "turns": len(turns),
            "avg_duration_ms": round(total_duration_ms / len(turns), 2) if turns else 0,
            "tool_calls": sum(tool_counts.values()),
            "errors": error_count,
        },
        "tool_counts": [
            {"name": name, "count": count}
            for name, count in sorted(tool_counts.items(), key=lambda item: -item[1])
        ],
        "recent": turns[:limit],
    }


def build_usage_report(
    path: Path | None,
    *,
    days: int = 30,
    limit: int = 200,
    user_id: str | None = None,
) -> dict[str, Any]:
    """Read the token log and return per-user + filtered aggregates and rows."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    rows: list[dict[str, Any]] = []
    for record in read_usage_log(path):
        raw_ts = record.get("ts")
        try:
            dt = datetime.fromisoformat(raw_ts)
        except (TypeError, ValueError):
            continue
        if dt < cutoff:
            continue

        model = normalize_model(record.get("model"))
        cached = int(record.get("cached_tokens") or 0)
        uncached = int(record.get("uncached_tokens") or 0)
        completion = int(record.get("completion_tokens") or 0)
        cost = usage_cost(model, raw_ts, cached, uncached, completion)
        rows.append(
            {
                "ts": raw_ts,
                "model": model,
                "raw_model": record.get("model"),
                "kind": record.get("kind"),
                "session_id": record.get("session_id"),
                "user_id": record.get("user_id"),
                "duration_ms": record.get("duration_ms"),
                "prompt_tokens": int(record.get("prompt_tokens") or 0),
                "completion_tokens": completion,
                "cached_tokens": cached,
                "uncached_tokens": uncached,
                "cost": cost,
                "day": dt.date().isoformat(),
            }
        )

    rows.sort(key=lambda item: item["ts"], reverse=True)

    by_user: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        key = row["user_id"] or "unknown"
        by_user[key]["cost"] += row["cost"]
        by_user[key]["requests"] += 1
        by_user[key]["prompt_tokens"] += row["prompt_tokens"]
        by_user[key]["completion_tokens"] += row["completion_tokens"]
        by_user[key]["cached_tokens"] += row["cached_tokens"]
        by_user[key]["uncached_tokens"] += row["uncached_tokens"]

    filtered = rows if user_id is None else [r for r in rows if r["user_id"] == user_id]

    totals: dict[str, Any] = defaultdict(int)
    by_model: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_kind: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    by_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    for row in filtered:
        totals["prompt_tokens"] += row["prompt_tokens"]
        totals["completion_tokens"] += row["completion_tokens"]
        totals["cached_tokens"] += row["cached_tokens"]
        totals["uncached_tokens"] += row["uncached_tokens"]
        totals["cost"] += row["cost"]
        totals["requests"] += 1

        by_model[row["model"]]["cost"] += row["cost"]
        by_model[row["model"]]["requests"] += 1
        by_model[row["model"]]["prompt_tokens"] += row["prompt_tokens"]
        by_model[row["model"]]["completion_tokens"] += row["completion_tokens"]

        by_kind[row["kind"] or "unknown"]["cost"] += row["cost"]
        by_kind[row["kind"] or "unknown"]["requests"] += 1

        by_day[row["day"]]["cost"] += row["cost"]
        by_day[row["day"]]["prompt_tokens"] += row["prompt_tokens"]
        by_day[row["day"]]["completion_tokens"] += row["completion_tokens"]
        by_day[row["day"]]["cached_tokens"] += row["cached_tokens"]
        by_day[row["day"]]["uncached_tokens"] += row["uncached_tokens"]
        by_day[row["day"]]["requests"] += 1

    return {
        "generated_at": now.isoformat(),
        "days": days,
        "filter_user_id": user_id,
        "totals": dict(totals),
        "by_user": [
            {
                "user_id": key,
                "cost": round(stats["cost"], 8),
                "requests": int(stats["requests"]),
                "prompt_tokens": int(stats["prompt_tokens"]),
                "completion_tokens": int(stats["completion_tokens"]),
                "cached_tokens": int(stats["cached_tokens"]),
                "uncached_tokens": int(stats["uncached_tokens"]),
            }
            for key, stats in sorted(by_user.items(), key=lambda item: -item[1]["cost"])
        ],
        "by_model": [
            {
                "model": model,
                "cost": round(stats["cost"], 8),
                "requests": int(stats["requests"]),
                "prompt_tokens": int(stats["prompt_tokens"]),
                "completion_tokens": int(stats["completion_tokens"]),
            }
            for model, stats in sorted(by_model.items())
        ],
        "by_kind": [
            {
                "kind": kind,
                "cost": round(stats["cost"], 8),
                "requests": int(stats["requests"]),
            }
            for kind, stats in sorted(by_kind.items())
        ],
        "daily": [
            {
                "day": day,
                "cost": round(stats["cost"], 8),
                "prompt_tokens": int(stats["prompt_tokens"]),
                "completion_tokens": int(stats["completion_tokens"]),
                "cached_tokens": int(stats["cached_tokens"]),
                "uncached_tokens": int(stats["uncached_tokens"]),
                "requests": int(stats["requests"]),
            }
            for day, stats in sorted(by_day.items())
        ],
        "recent": filtered[:limit],
    }
