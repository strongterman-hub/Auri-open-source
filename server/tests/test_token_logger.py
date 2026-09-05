from __future__ import annotations

import json
from pathlib import Path

from app.core.token_logger import TokenLogger, token_context, usage_breakdown


def test_usage_breakdown_openai_shape() -> None:
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 250,
        "total_tokens": 1250,
        "prompt_tokens_details": {"cached_tokens": 700},
    }

    breakdown = usage_breakdown(usage)

    assert breakdown["prompt_tokens"] == 1000
    assert breakdown["completion_tokens"] == 250
    assert breakdown["total_tokens"] == 1250
    assert breakdown["cached_tokens"] == 700
    assert breakdown["uncached_tokens"] == 300


def test_usage_breakdown_deepseek_shape() -> None:
    usage = {
        "prompt_tokens": 1000,
        "completion_tokens": 250,
        "total_tokens": 1250,
        "prompt_cache_hit_tokens": 600,
        "prompt_cache_miss_tokens": 400,
    }

    breakdown = usage_breakdown(usage)

    assert breakdown["cached_tokens"] == 600
    assert breakdown["uncached_tokens"] == 400


def test_token_logger_writes_jsonl_and_reads_context(tmp_dir: Path) -> None:
    path = tmp_dir / "logs" / "token_usage.jsonl"
    logger = TokenLogger(path)

    with token_context(kind="consolidation", session_id="s1"):
        logger.log(
            model="deepseek-v4-flash",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "prompt_cache_hit_tokens": 80,
                "prompt_cache_miss_tokens": 20,
            },
            duration_ms=12.345,
        )

    record = json.loads(path.read_text(encoding="utf-8").strip())
    assert record["model"] == "deepseek-v4-flash"
    assert record["kind"] == "consolidation"
    assert record["session_id"] == "s1"
    assert record["prompt_tokens"] == 100
    assert record["completion_tokens"] == 20
    assert record["cached_tokens"] == 80
    assert record["uncached_tokens"] == 20
    assert record["duration_ms"] == 12.35
