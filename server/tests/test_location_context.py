from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx

from app.agent.llm import LLMResponse
from app.agent.runner import AgentRunContext, BasicAgentRunner
from app.agent.tools import LocationTool, Tool
from app.memory.models import MemoryScope
from app.services.agent_service import AgentService
from app.services.device_store import DeviceStore
from app.core.chinese import normalize_value, to_simplified
from app.services.place_resolver import PlaceResolver
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
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
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
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
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
    # Coordinates stay server-side; the model only receives semantic place data.
    assert "latitude" not in payload
    assert "longitude" not in payload
    assert "recent_fixes" not in payload


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
class _FakePlaceHTTPClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[tuple[str, dict]] = []

    async def post(self, url, data=None, headers=None):
        self.posts.append((url, data))
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={
                "elements": [
                    {
                        "type": "way",
                        "id": 1,
                        "center": {"lat": 39.9603, "lon": 116.3597},
                        "tags": {
                            "amenity": "university",
                            "name": "北京师范大学",
                        },
                    },
                    {
                        "type": "way",
                        "id": 2,
                        "center": {"lat": 39.9596, "lon": 116.3623},
                        "tags": {"building": "yes", "name": "图书馆"},
                    },
                    {
                        "type": "node",
                        "id": 3,
                        "lat": 39.9598,
                        "lon": 116.3641,
                        "tags": {
                            "amenity": "fast_food",
                            "name": "麦当劳",
                        },
                    },
                ]
            },
        )

    async def get(self, url, params=None, headers=None):
        self.gets.append((url, params))
        if "bigdatacloud" in url:
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={
                    "city": "北京市",
                    "locality": "海淀区",
                    "principalSubdivision": "北京市",
                    "countryName": "中华人民共和国",
                },
            )
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "display_name": "北京市海淀区新街口外大街19号",
                "address": {
                    "city": "北京市",
                    "suburb": "海淀区",
                    "road": "新街口外大街",
                },
            },
        )


def test_place_resolver_prefers_semantic_poi_and_caches() -> None:
    client = _FakePlaceHTTPClient()
    resolver = PlaceResolver(client=client, timeout_seconds=2.0)

    result = asyncio.run(resolver.resolve(39.9595, 116.3619))
    calls_after_first = (len(client.posts), len(client.gets))
    again = asyncio.run(resolver.resolve(39.9595, 116.3619))

    assert result["place_name"] == "北京师范大学"
    assert result["place_type"] == "学校"
    assert "学校" in result["place_label"]
    assert result["city"] == "北京市"
    assert result["district"] == "海淀区"
    # First call tries the OSM map API, falls back to the fake Overpass
    # elements, then enriches the address once. The second call is cached.
    assert calls_after_first[0] == 1
    assert calls_after_first[1] == 2
    assert (len(client.posts), len(client.gets)) == calls_after_first
    assert again["place_label"] == result["place_label"]


class _FakePresence:
    def __init__(self, reading, *, history=None, online=True) -> None:
        self.reading = reading
        self.history = history if history is not None else ([reading] if reading else [])
        self.online = online

    def location_reading(self, _user_id, _agent_id="default"):
        return self.reading

    def location_history(self, _user_id, _agent_id="default", limit=2):
        return self.history[:limit]

    def is_online(self, _user_id, _agent_id="default", ttl_seconds=60):
        return self.online


class _FakePlaceResolver:
    async def resolve(self, latitude, longitude):
        return {
            "available": True,
            "place_name": "北京师范大学",
            "place_type": "学校",
            "place_label": "北京师范大学（学校）",
            "city": "北京市",
            "district": "海淀区",
            "address": "北京市海淀区新街口外大街19号",
            "source": "fake",
            "confidence": 0.9,
        }


class _CountingRequester:
    def __init__(self) -> None:
        self.calls = 0

    async def request(self, _user_id, _agent_id="default"):
        self.calls += 1
        return None


def _agent_service(presence, resolver) -> AgentService:
    from app.services.current_situation import CurrentSituationService
    service = AgentService.__new__(AgentService)
    service.presence_service = presence
    service.place_resolver = resolver
    service.current_situation_service = CurrentSituationService(presence, resolver)
    service.settings = SimpleNamespace(
        chat_location_fresh_seconds=240,
        location_context_max_points=12,
    )
    service.timezone_resolver = None
    return service


def test_current_location_text_returns_semantic_place_not_coordinates() -> None:
    now = datetime.now(timezone.utc)
    presence = _FakePresence(
        SimpleNamespace(
            latitude=39.9595,
            longitude=116.3619,
            reported_at=now - timedelta(seconds=30),
        )
    )
    service = _agent_service(presence, _FakePlaceResolver())
    requester = _CountingRequester()

    text = asyncio.run(
        service._current_location_text(
            MemoryScope(user_id="u1"),
            location_requester=requester,
        )
    )

    assert text is not None
    assert "北京师范大学（学校）" in text
    assert "39.9595" not in text
    assert "不要自行推断未给出的路线" in text
    # Location is grounding context, not a per-turn status readout. The model
    # otherwise opens unrelated answers (for example calorie estimates) with
    # "先定位：你在……" just because a fresh block is present.
    assert "不要求每轮回答都播报" in text
    assert "不要把定位当作开场白" in text
    assert "如果用户在本轮消息中已经说自己在哪里" in text
    assert "不要再用定位块纠正或重复播报位置" in text
    assert "回答涉及位置、通勤、附近环境或当前活动时，必须以此为准" not in text
    assert requester.calls == 0  # last fix is already fresh


def test_current_location_text_requests_fresh_fix_when_stale_and_online() -> None:
    presence = _FakePresence(
        SimpleNamespace(
            latitude=39.9595,
            longitude=116.3619,
            reported_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        )
    )
    service = _agent_service(presence, _FakePlaceResolver())
    requester = _CountingRequester()

    text = asyncio.run(
        service._current_location_text(
            MemoryScope(user_id="u1"),
            location_requester=requester,
        )
    )

    assert text is not None
    assert requester.calls == 1


def test_current_location_text_is_explicit_when_no_fix_exists() -> None:
    presence = _FakePresence(None, online=False)
    service = _agent_service(presence, _FakePlaceResolver())
    requester = _CountingRequester()

    text = asyncio.run(
        service._current_location_text(
            MemoryScope(user_id="u1"),
            location_requester=requester,
        )
    )

    assert text is not None
    assert "当前位置未知" in text
    assert requester.calls == 0


class _FailingOverpassHTTPClient:
    def __init__(self) -> None:
        self.gets = 0

    async def post(self, url, data=None, headers=None):
        raise httpx.ConnectTimeout("overpass unavailable")

    async def get(self, url, params=None, headers=None):
        self.gets += 1
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "city": "北京市",
                "locality": "海淀區",
                "principalSubdivision": "北京市",
                "countryName": "中华人民共和国",
            },
        )


def test_place_resolver_reuses_persisted_semantic_cache_when_overpass_fails(
    tmp_path: Path,
) -> None:
    cache_path = tmp_path / "place_semantic_cache.json"
    first = PlaceResolver(
        client=_FakePlaceHTTPClient(),
        timeout_seconds=2.0,
        persistent_cache_path=cache_path,
    )
    resolved = asyncio.run(first.resolve(39.9595, 116.3619))
    assert resolved["place_name"] == "北京师范大学"
    assert cache_path.exists()

    failing = PlaceResolver(
        client=_FailingOverpassHTTPClient(),
        timeout_seconds=2.0,
        persistent_cache_path=cache_path,
    )
    fallback = asyncio.run(failing.resolve(39.9595, 116.3619))

    assert fallback["place_name"] == "北京师范大学"
    assert fallback["place_label"] == resolved["place_label"]
    assert fallback["source"] == "semantic_cache"


_OSM_API_XML = """<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="1" lat="39.9590" lon="116.3610"/>
  <node id="2" lat="39.9600" lon="116.3610"/>
  <node id="3" lat="39.9600" lon="116.3630"/>
  <node id="4" lat="39.9590" lon="116.3630"/>
  <node id="5" lat="39.9500" lon="116.3500"/>
  <node id="6" lat="39.9700" lon="116.3500"/>
  <node id="7" lat="39.9700" lon="116.3750"/>
  <node id="8" lat="39.9500" lon="116.3750"/>
  <way id="10">
    <nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>
    <tag k="building" v="yes"/>
    <tag k="name" v="图书馆"/>
  </way>
  <way id="20">
    <nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/>
    <tag k="amenity" v="university"/>
    <tag k="name" v="北京师范大学"/>
  </way>
  <node id="30" lat="39.9592" lon="116.3621">
    <tag k="amenity" v="cafe"/>
    <tag k="name" v="咖啡馆"/>
  </node>
</osm>
""".encode("utf-8")


class _FakeOSMAPIHTTPClient:
    def __init__(self) -> None:
        self.osm_api_calls = 0
        self.overpass_calls = 0

    async def get(self, url, params=None, headers=None):
        if "api.openstreetmap.org" in url:
            self.osm_api_calls += 1
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                content=_OSM_API_XML,
                headers={"content-type": "application/xml"},
            )
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "city": "北京市",
                "locality": "海淀區",
                "principalSubdivision": "北京市",
                "countryName": "中华人民共和国",
            },
        )

    async def post(self, url, data=None, headers=None):
        self.overpass_calls += 1
        raise httpx.ConnectTimeout("overpass should not be needed")


def test_place_resolver_uses_official_osm_api_polygon_containment(tmp_path: Path) -> None:
    client = _FakeOSMAPIHTTPClient()
    resolver = PlaceResolver(client=client, timeout_seconds=2.0)

    result = asyncio.run(resolver.resolve(39.9595, 116.3619))

    assert result["place_name"] == "图书馆"
    assert result["place_type"] == "图书馆"
    assert result["parent_name"] == "北京师范大学"
    assert "北京师范大学" in result["place_label"]
    assert client.osm_api_calls == 1
    assert client.overpass_calls == 0


def test_agent_service_builds_activity_hypothesis_from_semantic_place() -> None:
    now = datetime.now(timezone.utc)
    presence = _FakePresence(
        SimpleNamespace(
            latitude=39.9595,
            longitude=116.3619,
            reported_at=now - timedelta(seconds=30),
        )
    )
    service = _agent_service(presence, _FakePlaceResolver())
    service.schedule_service = None
    capture = {
        "place": {
            "place_name": "图书馆",
            "place_type": "图书馆",
            "place_label": "图书馆（图书馆），位于北京师范大学（学校）",
        },
        "movement_classification": "stationary",
        "age_seconds": 30,
    }

    text = asyncio.run(
        service._current_activity_text(
            MemoryScope(user_id="u1"),
            capture,
            "我在图书馆看书",
        )
    )

    assert "USER-STATED" in text
    assert "在图书馆或学习" in text

    inferred = asyncio.run(
        service._current_activity_text(
            MemoryScope(user_id="u1"),
            capture,
            "",
        )
    )
    assert "NOT A FACT" in inferred
    assert "图书馆" in inferred
    assert "学习" in inferred or "看书" in inferred


def test_chinese_text_normalization() -> None:
    assert to_simplified("昌平區，北京市，中华人民共和国") == "昌平区，北京市，中华人民共和国"
    assert to_simplified("海國瑞業科技園") == "海国瑞业科技园"
    assert to_simplified(None) is None
    normalized = normalize_value(
        {
            "place_name": "東三旗村",
            "candidates": [{"name": "龍陽路", "distance_m": 12}],
            "confidence": 0.8,
        }
    )
    assert normalized["place_name"] == "东三旗村"
    assert normalized["candidates"][0]["name"] == "龙阳路"
    assert normalized["confidence"] == 0.8


class _TraditionalPlaceResolver(PlaceResolver):
    async def _resolve_uncached(self, latitude: float, longitude: float) -> dict:
        return {
            "available": True,
            "place_name": "海國瑞業科技園",
            "place_type": "公司/办公场所",
            "place_label": "海國瑞業科技園（公司/办公场所）",
            "parent_name": None,
            "parent_type": None,
            "address": "海淀區，北京市，中华人民共和国",
            "city": "北京市",
            "district": "海淀區",
            "region": "北京市",
            "country": "中华人民共和国",
            "source": "osm",
            "semantic_source": "osm",
            "confidence": 0.8,
            "candidates": [
                {"name": "海國瑞業科技園", "type": "公司/办公场所", "kind": "office", "distance_m": 10}
            ],
        }


def test_place_resolver_normalizes_osm_traditional_place_text() -> None:
    resolver = _TraditionalPlaceResolver(enabled=False)
    result = asyncio.run(resolver.resolve(40.053, 116.2934))
    assert result["place_name"] == "海国瑞业科技园"
    assert result["address"] == "海淀区，北京市，中华人民共和国"
    assert result["district"] == "海淀区"
    assert result["candidates"][0]["name"] == "海国瑞业科技园"


class _PersistentCacheResolver(PlaceResolver):
    async def _resolve_uncached(self, latitude: float, longitude: float) -> dict:
        return {"available": False, "reason": "no_place_resolved"}


def test_place_resolver_normalizes_persisted_traditional_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "place_semantic_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "entries": {
                    "40.0531,116.2934": {
                        "resolver_version": 2,
                        "relation": "nearby",
                        "place_name": "海國瑞業科技園",
                        "place_type": "公司/办公场所",
                        "place_label": "海國瑞業科技園",
                        "address": "海淀區，北京市",
                        "city": "北京市",
                        "district": "海淀區",
                        "region": "北京市",
                        "country": "中华人民共和国",
                        "source": "osm",
                        "confidence": 0.8,
                        "latitude": 40.0531,
                        "longitude": 116.2934,
                        "stored_at": time.time(),
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    resolver = _PersistentCacheResolver(persistent_cache_path=cache_path)
    result = asyncio.run(resolver.resolve(40.0531, 116.2934))
    assert result["source"] == "semantic_cache"
    assert result["place_name"] == "海国瑞业科技园"
    assert result["address"] == "海淀区，北京市"
    assert result["district"] == "海淀区"
