"""Per-generation situation evidence shared by drafting and final validation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4

from app.agent.activity import infer_current_activity
from app.agent.user_state import OwnerState, PLACE_LABELS, parse_owner_state


def location_freshness(age: float | None, fresh_seconds=900, stale_seconds=7200) -> str:
    if age is None:
        return "unknown"
    return "fresh" if age <= fresh_seconds else "stale" if age <= stale_seconds else "expired"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def owner_state_from_messages(messages, now: datetime, ttl_seconds=900) -> OwnerState:
    state = OwnerState()
    for message in messages or []:
        if message.get("role") != "user":
            continue
        try:
            at = _aware(datetime.fromisoformat(str(message.get("timestamp", "")).replace("Z", "+00:00")))
        except ValueError:
            continue
        if not 0 <= (_aware(now) - at).total_seconds() <= ttl_seconds:
            continue
        content = message.get("content", "")
        if isinstance(content, list):
            content = " ".join(str(x.get("text", "")) for x in content if isinstance(x, dict) and x.get("type") == "text")
        claim = parse_owner_state(str(content))
        if claim.place or claim.activity or claim.excluded_places or claim.excluded_activities:
            claim.source_refs = [str(message.get("id") or "current_message")]
            state.merge(claim)
    return state


@dataclass
class CurrentSituation:
    id: str = field(default_factory=lambda: uuid4().hex)
    owner: OwnerState = field(default_factory=OwnerState)
    place: dict | None = None
    age_seconds: float | None = None
    reported_at: str | None = None
    freshness: str = "unknown"
    refresh_status: str = "not_requested"
    movement: str | None = None
    fresh_seconds: int = 900
    stale_seconds: int = 7200
    revision: int = 1
    owner_from_history: bool = False

    def update_location_result(self, result: str) -> None:
        try:
            data = json.loads(result)
        except (ValueError, TypeError):
            return
        if not isinstance(data, dict) or data.get("available") is not True:
            self.refresh_status = "unavailable"
            return
        if not isinstance(data.get("age_seconds"), (int, float)):
            self.refresh_status = "invalid_tool_result"
            return
        self.revision += 1
        self.age_seconds = data.get("age_seconds")
        self.reported_at = data.get("reported_at")
        self.freshness = location_freshness(self.age_seconds, self.fresh_seconds, self.stale_seconds)
        self.place = {k: data.get(k) for k in ("place_name", "place_type", "place_label", "relation", "resolver_version")}
        self.place["source"] = data.get("place_source")
        self.refresh_status = "tool_result"

    def location_text(self) -> str:
        if self.age_seconds is None:
            return "用户当前位置未知：手机尚未上报位置，或用户未授予位置权限。不要猜测用户的地点。"
        label = str((self.place or {}).get("place_label") or "")
        if not label:
            label = "已有手机定位，但语义地点暂时无法解析，具体场所未知"
        prefix = "最近上报位置" if self.freshness == "fresh" else "最后已知位置"
        age = max(0, round(self.age_seconds))
        text = f"{prefix}：{label}；{age}秒前上报。"
        if self.freshness != "fresh":
            text += "该位置已陈旧，不能据此断言用户当前在哪里或做什么。"
        text += ("上报时间是服务器接收时间，手机测量时间和精度未知。"
                 "附近场所不等于已到场；地图办公场所不等于用户的公司。"
                 "不要自行推断未给出的路线、目的地或活动目的。"
                 "位置仅用于相关情境，不要求每轮回答都播报；不要把定位当作开场白。"
                 "如果用户在本轮消息中已经说自己在哪里，以用户说的为准，"
                 "不要再用定位块纠正或重复播报位置。用户当前自述和纠正优先。"
                 "回答只保留相关地点、时间和必要的不确定性，不讲服务器接收或字段等实现细节。")
        return text

    def activity_text(self, *, recent_user_text=None, active_event_title=None, snapshot_activity=None) -> str:
        if recent_user_text and not self.owner_from_history:
            self.owner.merge(parse_owner_state(recent_user_text))
        # Never let a stale fix produce a fresh activity hypothesis.
        place = self.place if self.freshness == "fresh" else None
        hypothesis = infer_current_activity(
            place=place, movement=self.movement if self.freshness == "fresh" else None,
            active_event_title=active_event_title, snapshot_activity=snapshot_activity,
            owner_state=self.owner,
        )
        return self.owner.format() + "\n\n" + hypothesis.format()

    def audit_dict(self) -> dict:
        place = self.place or {}
        return {
            "evidence_id": self.id, "version": 2, "revision": self.revision,
            "location_age_seconds": round(self.age_seconds) if self.age_seconds is not None else None,
            "location_freshness": self.freshness, "location_source": "device_report" if self.reported_at else "unknown",
            "place_relation": place.get("relation", "unknown"),
            "resolver_source": place.get("source"), "resolver_version": place.get("resolver_version"),
            "cache_version": place.get("cache_version"), "refresh_status": self.refresh_status,
            "owner": self.owner.audit_dict(),
        }


class CurrentSituationService:
    def __init__(self, presence=None, place_resolver=None, *, fresh_seconds=900, stale_seconds=7200, refresh_seconds=240):
        self.presence = presence
        self.place_resolver = place_resolver
        self.fresh_seconds = fresh_seconds
        self.stale_seconds = stale_seconds
        self.refresh_seconds = refresh_seconds

    async def build(self, scope, *, messages=None, requester=None, now=None) -> CurrentSituation:
        now = _aware(now or datetime.now(timezone.utc))
        situation = CurrentSituation(
            owner=owner_state_from_messages(messages, now),
            fresh_seconds=self.fresh_seconds, stale_seconds=self.stale_seconds,
            owner_from_history=messages is not None,
        )
        if self.presence is None:
            return situation
        reading = self.presence.location_reading(scope.user_id, scope.agent_id)
        def age(fix):
            return max(0.0, (now - _aware(fix.reported_at)).total_seconds()) if fix else None
        if requester is not None and (reading is None or age(reading) > self.refresh_seconds):
            if self.presence.is_online(scope.user_id, scope.agent_id, ttl_seconds=max(60, self.refresh_seconds)):
                situation.refresh_status = "timeout"
                try:
                    fresh = await requester.request(scope.user_id, scope.agent_id)
                    if fresh is not None:
                        reading = fresh
                        situation.refresh_status = "received"
                except Exception:
                    situation.refresh_status = "error"
        if reading is not None:
            situation.age_seconds = age(reading)
            situation.reported_at = _aware(reading.reported_at).isoformat()
            situation.freshness = location_freshness(situation.age_seconds, self.fresh_seconds, self.stale_seconds)
            if self.place_resolver is not None and situation.freshness != "expired":
                try:
                    situation.place = await self.place_resolver.resolve(reading.latitude, reading.longitude)
                except Exception:
                    pass
        return situation


def reply_conflicts(text: str, situation: CurrentSituation | None) -> list[str]:
    """Reject only identifiable present-user claims, not historical mentions."""
    if situation is None:
        return []
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"|‘[^’]*’', "", text)
    conflicts: list[str] = []
    if "DSML" in text or re.search(r"<\|(?:tool_call|tool_calls)|<｜.*?(?:invoke|calls)", text):
        conflicts.append("tool_protocol_in_reply")
    owner = situation.owner
    if situation.freshness == "fresh" and (situation.place or {}).get("place_label") and re.search(r"你(?:现在|目前|这会儿).{0,6}在哪|你在哪[里儿]?[?？]", text):
        conflicts.append("redundant_current_location_question")
    for clause in re.split(r"[。；;\n]", text):
        # Conditional suggestions are not present-location assertions.
        if re.search(r"如果|要是|假如|等你回|回公司后|下次回|以后回|昨天|前天|上次|以前|明天|后天|下周|到时候", clause) and not re.search(r"你现在|你目前|你这会儿", clause):
            continue
        if re.search(r"[?？]|吗", clause):
            continue
        for kind, label in PLACE_LABELS.items():
            words = {"employer": "公司|单位|办公室", "home": "家里|家", "campus": "学校|校园", "library": "图书馆", "dorm": "宿舍|寝室", "hospital": "医院", "hotel": "酒店"}[kind]
            assertion = re.search(r"你(?:现在|目前|这会儿|还|正|已经|就|应该)?(?:在|到了|回到)(?:" + words + r")", clause)
            if assertion and (kind in owner.excluded_places or (owner.place and owner.place != kind and not {owner.place, kind} <= {"library", "campus"})):
                conflicts.append("owner_place_conflict:" + kind)
            if assertion and not owner.place and kind not in owner.excluded_places:
                place_kind = {"学校": "campus", "图书馆": "library", "住处/宿舍": "dorm", "医院/医疗": "hospital", "酒店/住处": "hotel"}.get((situation.place or {}).get("place_type"))
                gps_support = situation.freshness == "fresh" and (situation.place or {}).get("relation") == "contains" and place_kind == kind
                if not gps_support:
                    conflicts.append("unsupported_current_place:" + kind)
        company_facility = re.search(r"(?:你们公司|你公司|公司的).{0,32}(?:午睡|休息室|午休)", clause)
        immediate_use = re.search(r"用上|去睡|去躺|躺一会|就去|就能|用起来", clause)
        current_employer_supported = owner.place == "employer"
        if company_facility and immediate_use and not current_employer_supported:
            conflicts.append("unsupported_current_company_facility")
        if re.search(r"你说了算", clause) and owner.correction:
            conflicts.append("unresolved_owner_correction")
        assistant_state = parse_owner_state(re.sub(r"^你", "我", clause))
        if assistant_state.activity in owner.excluded_activities:
            conflicts.append("owner_activity_conflict:" + assistant_state.activity)
    return sorted(set(conflicts))


def minimal_situation_reply(situation: CurrentSituation, user_text: str = "") -> str:
    if "employer" in situation.owner.excluded_places:
        return "是我把公司的情况套到现在了。你现在不在公司，我按这个前提接着说。"
    if re.search(r"午睡|午休|眯|睡一会", user_text):
        return "能找个舒服的地方歇一会儿，通常会轻松些。"
    return "我刚才对你当前的情况判断得太满了，这个前提还不能确定。"
