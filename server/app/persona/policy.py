from __future__ import annotations

import re

from app.persona.models import (
    BehaviorPolicy,
    PersonaPreset,
    RelationshipState,
    cap_style,
    normalize_style,
)


_TASK_MARKERS = (
    "帮我",
    "帮忙",
    "整理",
    "规划",
    "计划",
    "方案",
    "分析",
    "解释",
    "为什么",
    "怎么办",
    "如何",
    "步骤",
    "建议",
    "总结",
    "对比",
    "比较",
    "开发",
    "实现",
    "排查",
    "审查",
    "部署",
    "上线",
    "优化",
    "改进",
    "写一份",
    "做一份",
    "查一下",
    "搜索",
    "提醒我",
    "安排",
    "计算",
    "翻译",
)

_DETAILED_MARKERS = (
    "详细",
    "深入",
    "全面",
    "完整",
    "系统分析",
    "技术方案",
    "开发规划",
    "调研",
    "长文",
    "教程",
)

_SAFETY_MARKERS = (
    "紧急",
    "救命",
    "危险",
    "胸痛",
    "呼吸困难",
    "自杀",
    "不想活",
    "报警",
    "严重",
    "受伤",
    "流血",
)

_HEALTH_MARKERS = (
    "健康",
    "睡眠",
    "心率",
    "步数",
    "运动",
    "体重",
    "吃药",
    "生病",
    "发烧",
    "手环",
    "睡眠分",
)

_EMOTION_MARKERS = (
    "难过",
    "难受",
    "烦",
    "崩溃",
    "开心",
    "焦虑",
    "压力",
    "委屈",
    "生气",
    "累",
    "恶心",
    "退学",
    "不想活",
)

_REQUEST_MARKERS = (
    "帮我",
    "给我",
    "告诉我",
    "查一下",
    "搜索",
    "提醒",
    "安排",
    "设置",
    "打开",
    "关闭",
    "发一下",
)

_SILENT_EXACT = {
    "嗯",
    "嗯嗯",
    "好",
    "好的",
    "好吧",
    "行",
    "可以",
    "知道了",
    "收到",
    "晚安",
    "哈哈",
    "哈哈哈",
    "在",
    "在呢",
    "ok",
    "okay",
}


def normalize_text(text: str) -> str:
    return re.sub(r"[\s。.!！,，~～]+", "", (text or "").lower())


def is_question(text: str) -> bool:
    if "?" in (text or "") or "？" in (text or ""):
        return True
    return bool(
        re.search(
            r"(吗|呢|什么|怎么|为什么|哪[儿里]?|谁|多少|几点|是不是|能不能|可不可以)",
            text or "",
        )
    )


def is_task(text: str) -> bool:
    raw = text or ""
    return any(marker in raw for marker in _TASK_MARKERS)


def is_detailed_request(text: str) -> bool:
    raw = text or ""
    return any(marker in raw for marker in _DETAILED_MARKERS)


def is_safety(text: str) -> bool:
    return any(marker in (text or "") for marker in _SAFETY_MARKERS)


def mentions_health(text: str) -> bool:
    return any(marker in (text or "") for marker in _HEALTH_MARKERS)


def is_emotional(text: str) -> bool:
    return any(marker in (text or "") for marker in _EMOTION_MARKERS)


def is_request(text: str) -> bool:
    return any(marker in (text or "") for marker in _REQUEST_MARKERS)


def is_low_information(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False
    if normalized in _SILENT_EXACT:
        return True
    if len(normalized) <= 3 and not any(
        (is_question(text), is_task(text), is_safety(text), is_emotional(text))
    ):
        return True
    return False


def anchor_style(
    user_text: str,
    planned_style: str,
    *,
    has_attachment: bool = False,
    is_task_like: bool = False,
    is_safety_like: bool = False,
) -> str:
    planned = normalize_style(planned_style)
    if has_attachment or is_task_like or is_safety_like or is_detailed_request(user_text):
        return planned
    normalized_length = len(normalize_text(user_text))
    if normalized_length <= 8:
        return cap_style(planned, "micro")
    if normalized_length <= 25:
        return cap_style(planned, "short")
    if normalized_length <= 80:
        return cap_style(planned, "short") if planned in {"normal", "detailed"} else planned
    return planned


def build_behavior_policy(
    preset: PersonaPreset,
    relationship: RelationshipState | None,
    user_text: str,
    planned_style: str,
    *,
    has_attachment: bool = False,
    allow_silent: bool = True,
    open_loop_count: int = 0,
) -> BehaviorPolicy:
    question_like = is_question(user_text)
    task_like = is_task(user_text)
    safety_like = is_safety(user_text)
    emotional_like = is_emotional(user_text)
    low_info = is_low_information(user_text)
    health_like = mentions_health(user_text)
    detailed_like = is_detailed_request(user_text)

    if open_loop_count > 0:
        allow_question = False
    elif preset.question_level == "normal":
        allow_question = True
    elif preset.question_level == "low":
        allow_question = bool(task_like or safety_like)
    else:
        allow_question = False

    if preset.health_advice_level == "off":
        allow_health_advice = False
    elif preset.health_advice_level == "low":
        allow_health_advice = bool(health_like or task_like or safety_like)
    else:
        allow_health_advice = True

    allow_list = bool(task_like or detailed_like or has_attachment)
    should_silent = bool(
        allow_silent
        and low_info
        and not (question_like or task_like or safety_like or emotional_like or has_attachment)
    )
    lane_hint: str | None = None
    if safety_like or emotional_like:
        lane_hint = "fast"
    elif should_silent:
        lane_hint = "away" if len(normalize_text(user_text)) <= 3 else "normal"
    elif low_info and not question_like and not task_like:
        lane_hint = "normal"
    elif not question_like and not task_like and not health_like:
        lane_hint = "normal"

    reason_parts = []
    if should_silent:
        reason_parts.append("low_information_can_stay_silent")
    if task_like:
        reason_parts.append("task_keeps_utility")
    if safety_like:
        reason_parts.append("safety_never_silent")
    if open_loop_count:
        reason_parts.append("open_loop_blocks_new_question")
    if health_like:
        reason_parts.append("health_topic")

    return BehaviorPolicy(
        allow_question=allow_question,
        max_style=anchor_style(
            user_text,
            planned_style,
            has_attachment=has_attachment,
            is_task_like=task_like or detailed_like,
            is_safety_like=safety_like,
        ),
        allow_health_advice=allow_health_advice,
        allow_list=allow_list,
        allow_silent=bool(allow_silent and low_info),
        prefer_silent=should_silent,
        lane_hint=lane_hint,
        reason=", ".join(reason_parts) or "default_persona_policy",
        persona_id=preset.id,
        frequency_preset=preset.proactive_frequency_preset,
        open_loop_count=open_loop_count,
        is_task=task_like,
        is_safety=safety_like,
        is_question=question_like,
        is_low_information=low_info,
    )


def infer_relationship_updates(text: str) -> dict[str, object]:
    """Extract explicit, conservative relationship signals from a user message."""

    raw = (text or "").strip()
    updates: dict[str, object] = {}
    boundaries: list[str] = []

    for address_match in re.finditer(
        r"(?:叫我|我叫)([\u4e00-\u9fa5A-Za-z0-9_]{1,16})",
        raw,
    ):
        prefix = raw[: address_match.start()].rstrip()
        if prefix.endswith(("别", "不要", "不想")):
            continue
        value = address_match.group(1)
        if value not in {"我", "你", "他", "她"}:
            updates["address"] = value
            break
    if re.search(r"别叫我(哥|姐|叔|阿姨|宝宝|亲)", raw):
        boundaries.append("不要使用用户拒绝的亲密称呼")

    if any(word in raw for word in ("随意一点", "轻松一点", "别太正式", "随便聊")):
        updates["tone"] = "casual"
    if any(word in raw for word in ("温柔一点", "安静一点", "别太吵")):
        updates["tone"] = "quiet"
    if any(word in raw for word in ("活泼一点", "搞笑一点", "幽默一点")):
        updates["tone"] = "playful"

    if any(word in raw for word in ("少问一点", "别问那么多", "别总问", "不要问了", "别追问")):
        boundaries.append("少提问")
    if any(word in raw for word in ("别总提健康", "别老说健康", "少说健康", "别报数据")):
        boundaries.append("少主动播报健康数据")
    if any(word in raw for word in ("先安静", "安静点", "别主动找我", "少主动", "先别发")):
        boundaries.append("降低主动频率")
    if any(word in raw for word in ("多找我", "多主动", "主动一点", "多发一点")):
        boundaries.append("希望更主动")

    if boundaries:
        updates["boundaries"] = boundaries
    return updates


def extract_question_summary(assistant_text: str) -> str | None:
    text = (assistant_text or "").strip()
    if not text:
        return None
    if "?" not in text and "？" not in text:
        return None
    sentences = re.split(r"(?<=[。！？!?])\s*", text)
    for sentence in reversed(sentences):
        if "?" in sentence or "？" in sentence:
            return sentence.strip()[:120]
    return text[-120:]


def has_commitment(assistant_text: str) -> bool:
    text = assistant_text or ""
    return bool(
        re.search(
            r"(我(明天|晚点|之后|一会儿|回头|下次)[^。！？!?]{0,24}(提醒|叫你|告诉你|发你|帮你|找你)"
            r"|我帮你记着|我会提醒你|我晚点再问)",
            text,
        )
    )