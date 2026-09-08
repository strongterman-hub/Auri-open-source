from __future__ import annotations

import re


RESPONSE_STYLES = ("micro", "short", "normal", "detailed")

_MICRO_EXACT = {
    "谢谢",
    "谢谢你",
    "辛苦了",
    "明白",
    "懂了",
    "原来如此",
    "没事",
    "没关系",
}

_DETAILED_MARKERS = (
    "详细",
    "深入",
    "全面",
    "完整",
    "系统分析",
    "开发规划",
    "技术方案",
    "实现方案",
    "调研",
    "排查",
    "审查",
    "对比分析",
    "写一份",
    "做一份",
    "部署上线",
    "进行开发",
    "长文",
    "教程",
)

_IMPORTANT_MARKERS = (
    "紧急",
    "救命",
    "危险",
    "胸痛",
    "呼吸困难",
    "自杀",
    "不想活",
    "严重",
    "报警",
)

_NORMAL_MARKERS = (
    "分析",
    "解释",
    "原因",
    "怎么办",
    "如何",
    "方案",
    "步骤",
    "建议",
    "计划",
    "优化",
    "改进",
    "比较",
    "对比",
    "总结",
)


def normalize_response_style(value: str | None, *, fallback: str = "short") -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in RESPONSE_STYLES else fallback


def infer_response_style(
    text: str,
    *,
    has_attachment: bool = False,
    onboarding: bool = False,
) -> str:
    """Choose a safe local fallback without spending another model call."""

    if onboarding:
        return "micro"
    normalized = re.sub(r"[\s。.!！,，?？~～]+", "", text or "").lower()
    if normalized in _MICRO_EXACT:
        return "micro"
    if any(marker in (text or "") for marker in _DETAILED_MARKERS):
        return "detailed"
    if has_attachment or any(marker in (text or "") for marker in _IMPORTANT_MARKERS):
        return "normal"
    if any(marker in (text or "") for marker in _NORMAL_MARKERS):
        return "normal"
    return "short"


_STYLE_INSTRUCTIONS = {
    "micro": (
        "Reply with one natural sentence, usually about 5-35 Chinese characters. "
        "Acknowledge or respond directly; add no explanation, list, summary, or follow-up "
        "question unless it is essential."
    ),
    "short": (
        "Reply in 1-3 natural sentences, usually about 20-100 Chinese characters. "
        "Give the direct answer or reaction first. Mention only the one most useful point; "
        "do not expand every possibility, repeat the user, or mechanically ask a question "
        "at the end. Style example only: for '我今天好累', prefer '那先别硬撑了，歇一会儿吧。' "
        "over an unsolicited list of advice."
    ),
    "normal": (
        "Give the conclusion first, then only the necessary reason or next steps, usually "
        "about 80-220 Chinese characters. Use a short list only when it materially improves "
        "clarity. Do not add a recap or unrelated advice."
    ),
    "detailed": (
        "The user needs a genuinely detailed response. Structure it when useful, but still "
        "remove repetition, stock transitions, unnecessary background, and generic closing "
        "offers. Completeness for the requested task takes priority over brevity."
    ),
}


def response_style_instruction(style: str | None) -> str:
    selected = normalize_response_style(style)
    return (
        f"Selected response style: {selected.upper()}. "
        f"{_STYLE_INSTRUCTIONS[selected]} "
        "These are soft length targets, never a reason to omit information needed for "
        "correctness, safety, or successful task completion. Follow an explicit user request "
        "for a different format or level of detail."
    )
