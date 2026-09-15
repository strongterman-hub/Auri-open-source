from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.agent.llm import LLMResponse
from app.agent.runner import AgentRunContext, BasicAgentRunner
from app.agent.tools import LocationTool, Tool
from app.memory.models import MemoryScope
from app.services.device_store import DeviceStore
from app.services.presence_service import PresenceService


class FakeWeatherService:
    async def reverse_geocode(self, latitude: float, longitude: float) -> dict:
        return {
            "city": "北京",
            "region": "北京市",
            "country": "中国",
            "latitude": latitude,
            "longitude": longitude,
        }


def _reading(minutes: int, latitude: float, longitude: float):
    start = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        latitude=latitude,
        longitude=longitude,
        reported_at=start + timedelta(minutes=minutes),
    )


def test_movement_summary_is_conservative_and_explains_unknown() -> None:
    assert (
        LocationTool._movement_summary([_reading(0, 39.9, 116.4)])["classification"]
        == "unknown"
    )

    stationary = LocationTool._movement_summary(
        [
            _reading(0, 39.9, 116.4),
            _reading(5, 39.9, 116.4),
            _reading(10, 39.9, 116.4),
        ]
    )
    assert stationary["classification"] == "stationary"
    assert stationary["points"] == 3

    walking = LocationTool._movement_summary(
        [
            _reading(0, 39.9, 116.4),
            _reading(5, 39.905, 116.4),
            _reading(10, 39.91, 116.4),
        ]
    )
    assert walking["classification"] == "walking"

    cycling = LocationTool._movement_summary(
        [
            _reading(0, 39.9, 116.4),
            _reading(5, 39.912, 116.4),
            _reading(10, 39.924, 116.4),
        ]
    )
    assert cycling["classification"] == "cycling"

    driving = LocationTool._movement_summary(
        [
            _reading(0, 39.9, 116.4),
            _reading(5, 39.94, 116.4),
            _reading(10, 39.98, 116.4),
        ]
    )
    assert driving["classification"] == "driving"
    assert "route" in driving["rule"]


def test_location_tool_returns_recent_movement_context(tmp_dir: Path) -> None:
    store = DeviceStore(tmp_dir / "devices.db", location_history_limit=10)
    presence = PresenceService(device_store=store)
    start = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
    store.update_location("u1", "default", 39.900, 116.400, start)
    store.update_location(
        "u1", "default", 39.912, 116.400, start + timedelta(minutes=5)
    )
    store.update_location(
        "u1", "default", 39.924, 116.400, start + timedelta(minutes=10)
    )
    tool = LocationTool(
        presence=presence,
        weather_service=FakeWeatherService(),
        user_id="u1",
        agent_id="default",
        max_points=10,
    )

    payload = json.loads(asyncio.run(tool.execute()))

    assert payload["available"] is True
    assert payload["label"] == "北京，北京市，中国"
    assert payload["movement_summary"]["classification"] == "cycling"
    assert payload["movement_summary"]["points"] == 3
    assert len(payload["recent_fixes"]) == 3
    assert payload["recent_fixes"][0]["latitude"] == 39.9

class _SequenceLLM:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(
        self,
        messages,
        tools=None,
        max_tokens=None,
        model=None,
    ):
        self.calls.append(messages)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if isinstance(value, str):
            return LLMResponse(content=value)
        return LLMResponse(content=json.dumps(value, ensure_ascii=False))

    async def complete_structured(self, messages, *, max_tokens=4096):
        return await self.complete(messages, None, max_tokens)


class _LocationTool(Tool):
    name = "get_current_location"
    description = "location"
    parameters = {"type": "object", "properties": {}}
    calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        return json.dumps({"city": "北京", "movement_summary": {"classification": "stationary"}})


def test_grounding_can_fetch_location_with_real_tool_name() -> None:
    location = _LocationTool()
    llm = _SequenceLLM(
        "你正在学校那边。",
        {
            "verdict": "needs_tool",
            "tool_name": "get_current_location",
            "tool_args": {},
        },
        {"verdict": "rewrite", "message": "当前位置显示你在北京，具体地点无法确认。"},
    )
    context = AgentRunContext(
        session_id="s1",
        scope=MemoryScope(user_id="u1"),
        history=[{"id": "m1", "role": "user", "content": "我现在在哪？"}],
        memory_prompt="memory",
        tools=[location],
        current_message_ids=["m1"],
    )

    result = asyncio.run(
        BasicAgentRunner(llm, grounding_check=True).run(context)
    )

    assert location.calls == 1
    assert result.tool_results[0]["name"] == "get_current_location"
    assert result.text == "当前位置显示你在北京，具体地点无法确认。"


def test_location_activity_claims_trigger_grounding_check() -> None:
    from app.agent.situation import claim_needs_check

    assert claim_needs_check("你正在学校那边吧", [])
    assert claim_needs_check(
        "我现在在房间",
        [{"role": "user", "content": "我现在在哪？"}],
    )
