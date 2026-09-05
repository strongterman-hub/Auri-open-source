from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.agent.llm import LLMClient, LLMResponse, ToolCall
from app.agent.runner import AgentRunContext, BasicAgentRunner
from app.agent.tools import Tool
from app.core.turn_logger import TurnLogger
from app.memory.models import MemoryScope
from app.services.usage_service import build_turns_report


class FakeTool(Tool):
    name = "fake_tool"
    description = "test tool"
    parameters = {"type": "object", "properties": {}}

    async def execute(self, **kwargs):
        return "tool-result"


class FakeLLM(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages, tools=None, max_tokens=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(id="1", name="fake_tool", arguments={"a": 1})],
                usage={"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
            )
        return LLMResponse(
            content="final answer",
            usage={
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "prompt_tokens_details": {"cached_tokens": 40},
            },
        )

    async def stream(self, messages, tools=None, max_tokens=None):
        raise NotImplementedError


def test_runner_logs_turn_with_tool_call_and_timing(tmp_dir: Path) -> None:
    turn_logger = TurnLogger(tmp_dir / "agent_turns.jsonl")
    runner = BasicAgentRunner(FakeLLM(), turn_logger=turn_logger)
    context = AgentRunContext(
        session_id="s1",
        scope=MemoryScope(user_id="u1"),
        history=[],
        memory_prompt="memory",
        tools=[FakeTool()],
    )

    turn = asyncio.run(runner.run(context))

    assert turn.text == "final answer"
    lines = (tmp_dir / "agent_turns.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["status"] == "completed"
    assert record["user_id"] == "u1"
    assert record["session_id"] == "s1"
    assert record["llm_calls"] == 2
    assert record["tool_calls"][0]["name"] == "fake_tool"
    assert record["tool_calls"][0]["error"] is None
    assert record["prompt_tokens"] == 110
    assert record["completion_tokens"] == 51
    assert record["cached_tokens"] == 40
    assert record["uncached_tokens"] == 70
    assert record["response_chars"] == len("final answer")


def test_build_turns_report_aggregates(tmp_dir: Path) -> None:
    path = tmp_dir / "agent_turns.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "turn_id": "t1",
                        "started_at": "2026-08-21T12:00:00+00:00",
                        "duration_ms": 1200,
                        "session_id": "s1",
                        "user_id": "alice",
                        "model": "deepseek-v4-flash",
                        "llm_calls": 1,
                        "tool_calls": [
                            {"name": "memory", "duration_ms": 5, "error": None, "result_chars": 10},
                            {"name": "read_health_data", "duration_ms": 20, "error": "boom", "result_chars": 5},
                        ],
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "cached_tokens": 30,
                        "uncached_tokens": 70,
                        "status": "completed",
                        "response_chars": 20,
                    }
                )
            ]
        ),
        encoding="utf-8",
    )

    report = build_turns_report(path, days=30, limit=10)

    assert report["totals"]["turns"] == 1
    assert report["totals"]["tool_calls"] == 2
    assert report["totals"]["errors"] == 1
    assert report["tool_counts"] == [
        {"name": "memory", "count": 1},
        {"name": "read_health_data", "count": 1},
    ]
    assert report["recent"][0]["user_id"] == "alice"
