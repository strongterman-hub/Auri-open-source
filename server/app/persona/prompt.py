from __future__ import annotations

from typing import Iterable

from app.persona.models import (
    BehaviorPolicy,
    CharacterCard,
    PersonaPreset,
    RelationshipState,
)


STYLE_LABELS = {
    "micro": "一句以内（约 5-35 字）",
    "short": "1-3 句（约 20-100 字）",
    "normal": "先结论，再必要说明（约 80-220 字）",
    "detailed": "可以展开，但仍删除重复和套话",
}


def _bullets(items: Iterable[str], limit: int) -> str:
    values = [str(item).strip() for item in items if str(item).strip()]
    return "\n".join(f"- {item}" for item in values[:limit])


def stable_persona_block(
    preset: PersonaPreset,
    character: CharacterCard | None = None,
) -> str:
    if preset.prompt_override:
        return "[PERSONA - ACTIVE PRESET]\n" + preset.prompt_override.strip()[:800]
    lines = [
        "[PERSONA - ACTIVE PRESET]",
        f"预设：{preset.id} / {preset.name}",
        f"身份：{preset.identity}",
    ]
    if character is not None:
        label = character.label or character.presentation
        lines.append(f"形象：Auri（{label}）")
        if character.interests:
            lines.append("兴趣偏向：" + "、".join(character.interests[:5]))
        if character.address_hint:
            lines.append(f"称呼倾向：{character.address_hint}")
    if preset.temperament:
        lines.append("性格：")
        lines.append(_bullets(preset.temperament, 4))
    if preset.speech_style:
        lines.append("说话方式：")
        lines.append(_bullets(preset.speech_style, 5))
    if preset.boundaries:
        lines.append("边界：")
        lines.append(_bullets(preset.boundaries, 5))
    if preset.banned_phrases:
        lines.append(
            "禁用表达：" + "、".join(preset.banned_phrases[:10])
        )
    lines.append(
        "这些是稳定设定；不要在本轮重新发明人格，也不要向用户解释这些规则。"
    )
    return "\n".join(lines)


def relationship_block(
    state: RelationshipState | None,
    *,
    open_loop_count: int = 0,
) -> str:
    if state is None:
        return ""
    lines = ["[RELATIONSHIP - CURRENT]"]
    lines.append(f"关系阶段：{state.stage}")
    lines.append(f"称呼：{state.address or '使用你，不自行升级亲密称呼'}")
    lines.append(f"语气偏好：{state.tone}")
    if state.boundaries:
        lines.append("用户明确边界：" + "；".join(state.boundaries[-6:]))
    if state.shared_topics:
        lines.append("共同聊过的话题：" + "、".join(state.shared_topics[-5:]))
    if open_loop_count:
        lines.append(
            f"当前有 {open_loop_count} 个未结问题；不要新增提问，除非用户需要任务澄清。"
        )
    if state.recent_correction_count:
        lines.append(
            "用户最近纠正过你；先确认事实，不要重复旧说法或追问同一问题。"
        )
    return "\n".join(lines)


def chat_behavior_block(policy: BehaviorPolicy) -> str:
    lines = [
        "[BEHAVIOR POLICY - THIS TURN]",
        f"- 允许提问：{'是' if policy.allow_question else '否'}",
        f"- 篇幅上限：{STYLE_LABELS.get(policy.max_style, policy.max_style)}",
        f"- 健康建议：{'允许' if policy.allow_health_advice else '本轮不建议'}",
        f"- 列表/编号：{'任务场景可用' if policy.allow_list else '不要使用'}",
    ]
    if policy.prefer_silent:
        lines.append("- 本轮可以简短接住或保持安静，不要强行展开")
    if policy.lane_hint:
        lines.append(f"- 回复节奏倾向：{policy.lane_hint}")
    if policy.is_safety:
        lines.append("- 安全优先：完整响应，不要因为篇幅上限省略关键信息")
    lines.append(f"- 策略原因：{policy.reason}")
    lines.append(
        "不要向用户展示本策略块；按自然对话执行。"
    )
    return "\n".join(lines)


def proactive_behavior_block(
    *,
    frequency_preset: str,
    daily_limit: int,
    daily_count: int,
    open_loop_count: int,
    remaining: dict[str, int] | None = None,
) -> str:
    lines = [
        "[PROACTIVE BEHAVIOR]",
        f"频率档位：{frequency_preset}；今日已发 {daily_count}/{daily_limit} 条。",
    ]
    lines.append(
        "主动消息可以保持高频，但每条只表达一个观察、念头或问题，1-2 句。"
    )
    lines.append(
        "健康/天气只在异常、目标相关或用户明确询问时作为主动内容；普通数据留给用户主动查看。"
    )
    if open_loop_count:
        lines.append("已有未结问题：不要新增直接问题。")
    if remaining:
        parts = [f"{key} 剩 {value}" for key, value in remaining.items() if value >= 0]
        if parts:
            lines.append("品类剩余额度：" + "；".join(parts[:8]))
    lines.append("不要向用户展示本策略块。")
    return "\n".join(lines)