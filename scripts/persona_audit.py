from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def _day(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return "unknown"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).date().isoformat()


def _proactive_counts(data_dir: Path) -> dict[str, Counter]:
    path = data_dir / "proactive" / "decisions.db"
    counts: dict[str, Counter] = defaultdict(Counter)
    if not path.exists():
        return counts
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT category, decided_at FROM proactive_decisions WHERE should_message = 1"
    ).fetchall()
    connection.close()
    for row in rows:
        counts[_day(row["decided_at"] or "")][row["category"] or "unknown"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Auri persona/behavior aggregate audit")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--days", type=int, default=7)
    args = parser.parse_args()
    data_dir = Path(args.data_dir)

    replies = _load_jsonl(data_dir / "logs" / "chat_reply.jsonl")
    turns = _load_jsonl(data_dir / "logs" / "agent_turns.jsonl")
    proactive = _proactive_counts(data_dir)

    by_day = defaultdict(Counter)
    for item in replies:
        day = _day(item.get("ts", ""))
        by_day[day]["reply_plans"] += 1
        lane = item.get("lane") or "unknown"
        by_day[day][f"lane_{lane}"] += 1
        if item.get("outcome") == "silent":
            by_day[day]["silent"] += 1
        if item.get("policy_reason"):
            by_day[day]["persona_policy"] += 1

    turn_lengths = defaultdict(list)
    for item in turns:
        day = _day(item.get("started_at", ""))
        turn_lengths[day].append(int(item.get("response_chars") or 0))
    for day, values in turn_lengths.items():
        if values:
            by_day[day]["turn_count"] = len(values)
            by_day[day]["median_response_chars"] = sorted(values)[len(values) // 2]

    selected_days = sorted(by_day)[-max(1, args.days) :]
    output = {
        "days": {
            day: {
                **dict(by_day[day]),
                "proactive_categories": dict(proactive.get(day, {})),
            }
            for day in selected_days
        }
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()