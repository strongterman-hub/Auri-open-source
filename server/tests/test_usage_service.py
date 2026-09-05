from __future__ import annotations

import json
from pathlib import Path

from app.services.usage_service import build_usage_report


def test_build_usage_report_aggregates_and_costs(tmp_dir: Path) -> None:
    path = tmp_dir / "token_usage.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-08-21T02:00:00+00:00",  # Beijing 10:00 (peak)
                        "model": "deepseek-v4-flash",
                        "kind": "chat",
                        "session_id": "s1",
                        "user_id": "alice",
                        "prompt_tokens": 1_000_000,
                        "completion_tokens": 1_000_000,
                        "cached_tokens": 1_000_000,
                        "uncached_tokens": 0,
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-08-21T12:00:00+00:00",  # Beijing 20:00 (off-peak)
                        "model": "deepseek-v4-pro",
                        "kind": "consolidation",
                        "user_id": "bob",
                        "prompt_tokens": 1_000_000,
                        "completion_tokens": 1_000_000,
                        "cached_tokens": 0,
                        "uncached_tokens": 1_000_000,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    report = build_usage_report(path, days=30, limit=10)

    assert report["totals"]["requests"] == 2
    assert report["totals"]["prompt_tokens"] == 2_000_000
    assert report["totals"]["completion_tokens"] == 2_000_000
    # flash peak: 1M hit(0.10) + 1M out(9.0) = 9.10; pro off-peak: 1M miss(4.5) + 1M out(13.5) = 18.0
    assert round(report["totals"]["cost"], 8) == 27.10
    assert len(report["daily"]) == 1
    assert len(report["recent"]) == 2
    assert {u["user_id"] for u in report["by_user"]} == {"alice", "bob"}


def test_build_usage_report_filters_by_user(tmp_dir: Path) -> None:
    path = tmp_dir / "token_usage.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-08-21T12:00:00+00:00",
                        "model": "deepseek-v4-flash",
                        "kind": "chat",
                        "user_id": "alice",
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "cached_tokens": 0,
                        "uncached_tokens": 100,
                    }
                ),
                json.dumps(
                    {
                        "ts": "2026-08-21T12:00:00+00:00",
                        "model": "deepseek-v4-flash",
                        "kind": "chat",
                        "user_id": "bob",
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "cached_tokens": 0,
                        "uncached_tokens": 100,
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    report = build_usage_report(path, days=30, limit=10, user_id="alice")

    assert report["totals"]["requests"] == 1
    assert len(report["recent"]) == 1
    assert report["recent"][0]["user_id"] == "alice"
    # by_user still lists everyone so the dropdown remains complete.
    assert {u["user_id"] for u in report["by_user"]} == {"alice", "bob"}
