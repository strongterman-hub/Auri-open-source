from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.agent.user_state import OwnerState, parse_owner_state


_PLACE_ACTIVITY: dict[str, tuple[str, str, float]] = {
    "图书馆": ("studying", "可能在图书馆学习、看书或查资料", 0.68),
    "教学楼": ("class_or_study", "可能在上课、自习或在教学楼办事", 0.62),
    "食堂": ("meal", "可能在食堂吃饭", 0.72),
    "学校": ("on_campus", "可能在学校上课、自习或处理校园事务", 0.55),
    "办公楼/办公场所": ("at_place", "可能在办公场所附近或停留，是否在工作未知", 0.45),
    "公司/办公场所": ("at_place", "可能在办公场所附近或停留，是否在工作未知", 0.45),
    "住处/居住区": ("at_place", "可能在居住区停留，是否在自己家未知", 0.45),
    "住处/宿舍": ("at_place", "可能在宿舍楼停留，是否为本人住处未知", 0.45),
    "酒店/住处": ("hotel_stay", "可能在酒店休息或在旅途中", 0.55),
    "餐饮店": ("meal", "可能在吃饭、喝东西或休息", 0.62),
    "咖啡店": ("cafe", "可能在喝咖啡、休息或聊天", 0.6),
    "运动场所": ("exercising", "可能在运动或健身", 0.68),
    "医院/医疗": ("medical", "可能在看病、体检或陪诊", 0.55),
    "车站": ("traveling", "可能在等车或出行途中", 0.6),
    "机场": ("traveling", "可能在赶飞机或出行途中", 0.65),
    "商店/商场": ("shopping", "可能在购物或逛街", 0.52),
    "商店": ("shopping", "可能在购物或逛街", 0.5),
    "公园/休闲区": ("outdoors", "可能在公园散步或户外休闲", 0.5),
    "景点": ("tourism", "可能在景点游玩", 0.5),
    "银行": ("errand", "可能在办理银行业务", 0.55),
    "公共服务": ("errand", "可能在办理公共事务", 0.5),
    "休闲场所": ("leisure", "可能在休闲娱乐", 0.5),
    "教学楼内建筑": ("class_or_study", "可能在上课或自习", 0.6),
    "建筑": ("at_place", "可能在当前建筑内办事或停留", 0.45),
}


_USER_STATED_LABELS: dict[str, str] = {
    "class": "在上课",
    "meeting": "在开会",
    "studying": "在图书馆或学习",
    "working": "在工作/上班",
    "meal": "在吃饭",
    "at_home": "在家或住处",
    "commuting": "在路上/通勤途中",
    "exercising": "在运动",
    "leisure": "在休息或休闲",
    "medical": "在医院或就医",
    "hotel_stay": "在酒店",
    "on_campus": "在学校",
    "at_workplace": "在公司",
    "arrived": "已经到达",
    "post_exercise": "运动已结束",
    "at_library": "在图书馆",
    "at_hospital": "在医院",
    "at_hotel": "在酒店",
}

@dataclass
class ActivityHypothesis:
    state: str = "unknown"
    label: str | None = None
    confidence: float = 0.0
    reasons: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    user_stated: bool = False

    def format(self) -> str:
        if self.user_stated:
            stated = _USER_STATED_LABELS.get(self.state, self.label or self.state)
            lines = [
                "[CURRENT ACTIVITY — USER-STATED, AUTHORITATIVE FOR THIS TURN]",
                "The user explicitly stated this in the current message. Treat it "
                "as true for this turn, answer in that context, and do not override "
                "or correct it with GPS or activity inference. You may refer to it "
                "naturally; do not read this block back as a status report.",
                f"User-stated current state: {stated}.",
            ]
            if self.reasons:
                lines.append("Source: " + "；".join(self.reasons) + "。")
            return "\n".join(lines)

        header = (
            "[CURRENT ACTIVITY HYPOTHESIS — NOT A FACT]\n"
            "This is an inference, not a confirmed event. Use it only to choose "
            "timing, tone and topic. Do not tell the user that they are definitely "
            "doing this; user corrections in the current message always outrank it."
        )
        if not self.label or self.state == "unknown":
            return (
                header
                + "\nNo reliable evidence supports a current-activity guess. "
                "Do not pretend to know what the user is doing right now."
            )
        lines = [
            header,
            f"Possible current activity: {self.label} "
            f"(confidence {self.confidence:.2f}).",
        ]
        if self.reasons:
            lines.append("Evidence: " + "；".join(self.reasons) + "。")
        if self.evidence:
            lines.append("Evidence refs: " + ", ".join(self.evidence))
        return "\n".join(lines)


def infer_current_activity(
    *,
    place: dict[str, Any] | None = None,
    movement: str | None = None,
    local_now: datetime | None = None,
    active_event_title: str | None = None,
    recent_user_text: str | None = None,
    snapshot_activity: dict[str, Any] | None = None,
    owner_state: OwnerState | None = None,
) -> ActivityHypothesis:
    """Infer a conservative current-activity hypothesis from semantic context.

    The order of precedence is:
    1. explicit words in the user's current/recent message;
    2. an active calendar event (still explicitly labelled as a plan);
    3. fresh movement plus semantic place type;
    4. semantic place type alone.
    """
    now = local_now
    owner = owner_state or parse_owner_state(str(recent_user_text or ""))
    state = owner.activity or {
        "home": "at_home", "dorm": "at_home", "campus": "on_campus",
        "library": "at_library", "employer": "at_workplace", "hospital": "at_hospital",
        "hotel": "at_hotel",
    }.get(owner.place)
    if state:
        return ActivityHypothesis(
            state=state, label=_label_for_state(state), confidence=0.8,
            reasons=["用户明确陈述当前情况"], evidence=owner.source_refs or ["user_message"],
            user_stated=True,
        )

    if snapshot_activity and snapshot_activity.get("state"):
        state = str(snapshot_activity.get("state"))
        if state in {"sleeping", "exercising", "commuting", "meal", "in_event"} and state not in owner.excluded_activities:
            mapped = {
                "sleeping": ("sleeping", "可能正在睡觉或休息"),
                "exercising": ("exercising", "可能正在运动"),
                "commuting": ("commuting", "可能在通勤或外出途中"),
                "meal": ("meal", "可能在吃饭"),
                "in_event": ("in_event", "可能正在参加日程中的活动"),
            }[state]
            return ActivityHypothesis(
                state=mapped[0],
                label=mapped[1],
                confidence=float(snapshot_activity.get("confidence") or 0.6),
                reasons=[str(snapshot_activity.get("reason") or "情境信号推断")],
                evidence=list(snapshot_activity.get("evidence_refs") or []),
            )

    if active_event_title:
        return ActivityHypothesis(
            state="in_event",
            label=f"可能正在参加「{active_event_title}」",
            confidence=0.7,
            reasons=["日程表显示这个时段有安排（计划，不代表已到场）"],
            evidence=["schedule"],
        )

    place_type = str((place or {}).get("place_type") or "")
    place_name = str((place or {}).get("place_name") or "")
    mapped = _PLACE_ACTIVITY.get(place_type)
    if (place or {}).get("relation") in {"nearby", "administrative", "unknown"}:
        mapped = None
    if owner.excluded_places:
        # A place exclusion must not be overwritten by a map-derived activity.
        mapped = None
    if movement in {"walking", "cycling", "driving"}:
        return ActivityHypothesis(
            state="commuting",
            label="可能在通勤、赶路或外出途中",
            confidence=0.6,
            reasons=["最近位置变化显示在移动"],
            evidence=["movement"],
        )

    if mapped:
        return ActivityHypothesis(
            state=mapped[0],
            label=mapped[1],
            confidence=mapped[2],
            reasons=[f"语义位置={place_name or place_type}（{place_type}）"],
            evidence=["semantic_place"],
        )

    if movement == "stationary":
        return ActivityHypothesis(
            state="stationary",
            label="可能停留在某个地方，但具体在做什么无法确认",
            confidence=0.35,
            reasons=["最近位置显示停留，但缺少场所或日程证据"],
            evidence=["movement"],
        )

    return ActivityHypothesis()


def _label_for_state(state: str) -> str:
    mapping = {
        "class": "可能在上课",
        "meeting": "可能在开会",
        "studying": "可能在学习或看书",
        "working": "可能在工作或办公",
        "meal": "可能在吃饭",
        "at_home": "可能在家或宿舍",
        "commuting": "可能在通勤或外出途中",
        "exercising": "可能在运动",
        "leisure": "可能在休息或休闲",
        "medical": "可能在医院或就医",
        "hotel_stay": "可能在酒店",
        "on_campus": "可能在学校",
        "at_workplace": "可能在公司",
        "arrived": "已经到达",
        "post_exercise": "运动已结束",
    }
    return mapping.get(state, f"可能在{state}")
