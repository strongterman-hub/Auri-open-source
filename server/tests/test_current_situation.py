from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.activity import infer_current_activity
from app.agent.llm import LLMClient, LLMResponse
from app.agent.runner import AgentRunContext, BasicAgentRunner
from app.agent.situation import is_correction
from app.agent.user_state import parse_owner_state
from app.memory.models import MemoryScope
from app.services.current_situation import (
    CurrentSituation, CurrentSituationService, owner_state_from_messages, reply_conflicts,
)
from app.services.place_resolver import PlaceResolver, _ScoredPlace, _place_result


@pytest.mark.parametrize("text,place,activity,excluded", [
    ("我现在不在公司，你不知道？", None, None, {"employer"}),
    ("我现在不在公司你不知道？", None, None, {"employer"}),
    ("我不在公司，在图书馆", "library", None, {"employer"}),
    ("明天又要上班了", None, None, set()),
    ("我一般在公司午睡", None, None, set()),
    ("我每天在家办公", None, None, set()),
    ("我想在家办公", None, None, set()),
    ("我可能在公司", None, None, set()),
    ("我昨天在家，今天在学校", "campus", None, set()),
    ("昨天在家，今天在学校", "campus", None, set()),
    ("如果我在公司就能午睡了", None, None, set()),
    ("你在公司吗", None, None, set()),
    ("我在家办公", "home", "working", set()),
    ("我不是不在公司", None, None, set()),
    ("我朋友说他在公司", None, None, set()),
    ("我同事在公司", None, None, set()),
    ("他说我在公司", None, None, set()),
    ("我说的是“在公司”，我现在在家", "home", None, set()),
    ("我现在在公司吗？", None, None, set()),
    ("我正在开会，晚点说", None, "meeting", set()),
    ("我不在公司，我在家办公", "home", "working", {"employer"}),
])
def test_owner_statement_scope(text, place, activity, excluded):
    result = parse_owner_state(text)
    assert result.place == place
    assert result.activity == activity
    assert result.excluded_places == excluded


def test_location_correction_question_is_preserved():
    text = "我现在不在公司，你不知道？"
    assert is_correction(text)
    hypothesis = infer_current_activity(recent_user_text=text)
    assert not hypothesis.user_stated
    assert hypothesis.state != "working"


def test_no_company_does_not_exclude_remote_work():
    state = parse_owner_state("我不在公司，我在家办公")
    assert infer_current_activity(owner_state=state).state == "working"
    assert state.excluded_activities == set()


def test_negative_information_alone_is_not_negative_feedback():
    assert not is_correction("我不在公司，我在家办公")


def test_place_statement_does_not_invent_activity():
    result = infer_current_activity(recent_user_text="我在图书馆")
    assert result.state == "at_library"
    assert "User-stated current state: 在图书馆" in result.format()
    assert "学习" not in result.format()


def test_delayed_message_is_not_reasserted_as_current():
    situation = asyncio.run(CurrentSituationService().build(
        MemoryScope(user_id="fake"), messages=[message("我在公司", 901)], now=NOW))
    assert "User-stated current state" not in situation.activity_text(recent_user_text="我在公司")


def test_location_tool_refresh_updates_evidence_without_losing_owner_constraints():
    situation = CurrentSituation(owner=parse_owner_state("我不在公司"))
    situation.update_location_result(json.dumps({"available": True, "age_seconds": 3,
        "reported_at": NOW.isoformat(), "place_name":"图书馆", "place_label":"图书馆",
        "place_type":"图书馆", "relation":"contains", "resolver_version":2}))
    assert situation.revision == 2 and situation.freshness == "fresh"
    assert situation.owner.excluded_places == {"employer"}
    assert not reply_conflicts("最近定位在图书馆", situation)
    assert reply_conflicts("你现在在公司", situation)


NOW = datetime(2026, 10, 19, 7, 0, tzinfo=timezone.utc)


def message(text, seconds=0, id="m1"):
    return {"id": id, "role": "user", "content": text,
            "timestamp": (NOW-timedelta(seconds=seconds)).isoformat()}


def test_recent_negative_supersedes_old_positive_without_extraction():
    state = owner_state_from_messages([
        message("我在公司", 200, "old"), message("我不在公司，你不知道？", 3, "new")
    ], NOW)
    assert state.place is None
    assert state.excluded_places == {"employer"}
    assert "new" in state.source_refs
    assert owner_state_from_messages([message("我不在公司", 901)], NOW).excluded_places == set()


def test_newer_positive_can_replace_old_exclusion():
    state = owner_state_from_messages([
        message("我不在公司", 300), message("我现在回到公司了", 1, "new")
    ], NOW)
    assert state.place == "employer" and not state.excluded_places


class Presence:
    def __init__(self, age=30):
        self.reading = SimpleNamespace(latitude=10.5, longitude=20.5,
                                       reported_at=NOW-timedelta(seconds=age))
    def location_reading(self, user, agent="default"):
        return self.reading
    def is_online(self, *args, **kwargs):
        return True


class Resolver:
    def __init__(self):
        self.calls = 0
    async def resolve(self, *args):
        self.calls += 1
        return {"place_name": "示例大学", "place_label": "示例大学（学校）",
                "place_type": "学校", "relation": "contains", "resolver_version": 2}


@pytest.mark.parametrize("age,freshness", [(30,"fresh"), (901,"stale"), (7201,"expired")])
def test_location_freshness_and_activity_share_evidence(age, freshness):
    resolver = Resolver()
    service = CurrentSituationService(Presence(age), resolver)
    situation = asyncio.run(service.build(MemoryScope(user_id="fake"), now=NOW))
    assert situation.freshness == freshness
    if freshness != "fresh":
        assert "最后已知" in situation.location_text()
        assert "可能在学校" not in situation.activity_text()
    if freshness == "expired":
        assert resolver.calls == 0


def test_account_isolation_and_audit_redaction():
    service = CurrentSituationService(Presence(), Resolver())
    a = asyncio.run(service.build(MemoryScope(user_id="a"), messages=[message("我不在公司")], now=NOW))
    b = asyncio.run(service.build(MemoryScope(user_id="b"), messages=[], now=NOW))
    assert not b.owner.excluded_places
    assert a.id != b.id
    audit = json.dumps(a.audit_dict(), ensure_ascii=False)
    assert "示例大学" not in audit and "latitude" not in audit and "longitude" not in audit
    assert "我不在公司" not in audit


def test_location_refresh_has_bounded_timeout_and_retains_last_known():
    class Requester:
        async def request(self, *args):
            return None
    situation = asyncio.run(CurrentSituationService(Presence(1000), Resolver()).build(
        MemoryScope(user_id="fake"), requester=Requester(), now=NOW))
    assert situation.refresh_status == "timeout" and situation.freshness == "stale"


@pytest.mark.parametrize("text,conflict", [
    ("你现在在公司，去休息室睡吧", True),
    ("你们公司不是有午睡的地方嘛，用上就舒服了", True),
    ("你们公司有午睡的地方", False),
    ("如果回公司，可以去午睡室", False),
    ("明天你在公司时，可以去午睡室", False),
    ("我刚才说“你现在在公司”是错的", False),
    ("你说了算", True),
    ("不在公司也没关系，午间歇一会儿会轻松些", False),
    ("你现在在家", True),
])
def test_final_reply_conflicts_are_specific(text, conflict):
    situation = CurrentSituation(owner=parse_owner_state("我不在公司，你不知道？"))
    assert bool(reply_conflicts(text, situation)) is conflict


class BadRewriteLLM(LLMClient):
    def __init__(self):
        self.calls = 0
    async def complete(self, messages, tools=None, max_tokens=None, model=None):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(content="你现在在公司，去午睡吧")
        return LLMResponse(content=json.dumps({"verdict":"rewrite", "message":"你现在在公司，去午睡吧"}))


def test_rewrite_is_checked_before_final_delivery(tmp_path):
    from app.core.turn_logger import TurnLogger
    situation = CurrentSituation(owner=parse_owner_state("我不在公司"))
    llm = BadRewriteLLM()
    context = AgentRunContext(session_id="synthetic",scope=MemoryScope(user_id="fake"),
        history=[message("我不在公司")],memory_prompt="",current_situation=situation)
    path = tmp_path/"turn.jsonl"
    result = asyncio.run(BasicAgentRunner(llm, grounding_check=True, turn_logger=TurnLogger(path)).run(context))
    assert "你现在在公司" not in result.text
    assert "不在公司" in result.text
    assert llm.calls == 3
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["grounding"]["status"] == "blocked_situation_conflict"
    assert record["situation"]["evidence_id"] == situation.id


@pytest.mark.parametrize("has_location", [False, True])
@pytest.mark.parametrize("draft", ["待核实位置", "<｜DSML｜calls>get_current_location</｜DSML｜calls>"])
def test_location_query_fallback_uses_only_available_evidence(has_location, draft):
    class Block(LLMClient):
        async def complete(self, *args, **kwargs):
            return LLMResponse(content=json.dumps({"verdict":"block"}))
    situation=CurrentSituation(place={"place_label":"示例图书馆","relation":"contains"} if has_location else None,
        freshness="fresh" if has_location else "unknown",age_seconds=120 if has_location else None)
    context=AgentRunContext(session_id="synthetic",scope=MemoryScope(user_id="fake"),
        history=[message("我现在在哪儿")],memory_prompt="",current_situation=situation)
    audit={}
    text=asyncio.run(BasicAgentRunner(Block(),grounding_check=True)._ground_reply(context,draft,[],[],{},audit))
    assert audit["status"] == "fallback_location_evidence"
    assert ("示例图书馆" in text) is has_location
    assert "我刚查" not in text
    assert "DSML" not in text


def square(a,b,c,d):
    return [(a,b),(a,d),(c,d),(c,b),(a,b)]


def test_nearby_office_cannot_parent_campus():
    school = _ScoredPlace("示例大学","学校","university",5,enclosing=True,area_m2=100,
                          polygon=square(0,0,10,10))
    office = _ScoredPlace("行政楼","办公楼/办公场所","office",4,distance_m=40)
    result = _place_result(PlaceResolver._select_osm_api_places([school,office],5,5),5,5,source="synthetic")
    assert result["parent_name"] is None
    assert "行政楼" not in result["place_label"]


def test_real_polygon_hierarchy_is_retained():
    library = _ScoredPlace("图书馆","图书馆","library",3,enclosing=True,area_m2=1,
                           polygon=square(1,1,2,2))
    school = _ScoredPlace("示例大学","学校","university",5,enclosing=True,area_m2=100,
                          polygon=square(0,0,10,10))
    result = _place_result([library,school],1.5,1.5,source="synthetic")
    assert result["parent_name"] == "示例大学"
    assert result["relation"] == "contains"


def test_overlapping_regions_are_not_parent_and_child():
    a = _ScoredPlace("图书馆","图书馆",None,3,enclosing=True,area_m2=9,polygon=square(0,0,3,3))
    b = _ScoredPlace("大学","学校",None,5,enclosing=True,area_m2=16,polygon=square(1,1,5,5))
    assert _place_result([a,b],2,2,source="synthetic")["parent_name"] is None


class OfflineResolver(PlaceResolver):
    async def _resolve_uncached(self, *args):
        return {"available":False}


def test_legacy_cache_cannot_restore_wrong_parent(tmp_path):
    path=tmp_path/"cache.json"
    path.write_text(json.dumps({"entries":{"0,0":{"place_name":"wrong","place_label":"wrong parent",
        "latitude":0,"longitude":0,"stored_at":9999999999}}}),encoding="utf-8")
    result=asyncio.run(OfflineResolver(persistent_cache_path=path).resolve(0,0))
    assert result.get("place_name") is None


def test_cross_building_cache_reuse_requires_geometry(tmp_path):
    resolver=OfflineResolver(persistent_cache_path=tmp_path/"cache.json")
    resolver._remember_semantic(1,1,{"place_name":"楼A","place_label":"楼A","place_type":"建筑",
        "resolver_version":2,"relation":"contains","_polygon":square(.9999,.9999,1.0001,1.0001)})
    assert resolver._lookup_semantic_cache(1,1) is not None
    assert resolver._lookup_semantic_cache(1.0003,1) is None


def test_proactive_snapshot_reuses_one_situation_for_draft_and_check():
    from app.proactive.context import SituationSnapshot
    from app.proactive.engine import ProactiveEngine
    class Sessions:
        async def list_by_user(self, uid):
            return [SimpleNamespace(agent_id="default",end_reason=None,updated_at=NOW,
                messages=[message("我不在公司，你不知道？")])]
    engine = ProactiveEngine.__new__(ProactiveEngine)
    engine.session_service = Sessions()
    resolver = Resolver()
    # Use a current timestamp for this integration path, which uses the clock.
    presence = Presence()
    presence.reading.reported_at = datetime.now(timezone.utc)
    engine.current_situation_service = CurrentSituationService(presence,resolver)
    snapshot = SituationSnapshot(user_id="fake",generated_at=NOW,timezone="Asia/Shanghai")
    async def run():
        first = await engine._build_current_situation(MemoryScope(user_id="fake"),snapshot)
        second = await engine._build_current_situation(MemoryScope(user_id="fake"),snapshot)
        return first,second
    a,b=asyncio.run(run())
    assert a is b and resolver.calls == 1
    assert snapshot.audit_dict()["current_situation"]["evidence_id"] == a.id


def test_proactive_rewrite_cannot_reintroduce_rejected_location():
    from app.proactive.context import SituationSnapshot
    from app.proactive.engine import ProactiveEngine
    from app.proactive.models import ProactiveDecision, ProactiveCategory, ConversationIntent, TriggerType, ProactivePhase
    class Rewrite(LLMClient):
        async def complete(self,*args,**kwargs):
            return LLMResponse(content=json.dumps({"verdict":"rewrite","message":"你现在在公司，去午睡吧"}))
    engine=ProactiveEngine.__new__(ProactiveEngine)
    engine.llm=Rewrite()
    engine.context_builder=object()
    engine.settings=SimpleNamespace(proactive_continuity_check_enabled=True,
        proactive_continuity_hold_seconds=900,proactive_continuity_check_max_tokens=256)
    engine.pacing_store=SimpleNamespace(continuity_hold_active=lambda *args:False)
    situation=CurrentSituation(owner=parse_owner_state("我不在公司，你不知道？"))
    snapshot=SituationSnapshot(user_id="fake",generated_at=NOW,timezone="Asia/Shanghai",current_situation=situation)
    decision=ProactiveDecision(user_id="fake",agent_id="default",phase=ProactivePhase.daily,
        category=ProactiveCategory.health_insight,trigger_type=TriggerType.time,
        should_message=True,message="午间歇一会儿吧",conversation_intent=ConversationIntent.share)
    asyncio.run(engine._apply_continuity_preflight(MemoryScope(user_id="fake"),decision,
        situation_snapshot=snapshot,memory_snapshot=SimpleNamespace(memory=[],user=[])))
    assert not decision.should_message
    assert decision.message is None
