from app.agent.response_style import (
    infer_response_style,
    normalize_response_style,
    response_style_instruction,
)
from app.agent.runner import AgentRunContext, _system_content
from app.memory.models import MemoryScope
from app.proactive.engine import PROACTIVE_SYSTEM_PROMPT


def test_infer_response_style_uses_minimum_sufficient_band() -> None:
    assert infer_response_style("谢谢你") == "micro"
    assert infer_response_style("明天会下雨吗？") == "short"
    assert infer_response_style("请分析一下为什么会这样？") == "normal"
    assert infer_response_style("我发了个文件", has_attachment=True) == "normal"
    assert infer_response_style("我胸痛而且呼吸困难") == "normal"
    assert infer_response_style("请详细分析并给我一份技术方案") == "detailed"
    assert infer_response_style("好的", onboarding=True) == "micro"


def test_response_style_normalization_and_instruction() -> None:
    assert normalize_response_style("DETAILED") == "detailed"
    assert normalize_response_style("unknown") == "short"
    short = response_style_instruction("short")
    assert "Selected response style: SHORT" in short
    assert "1-3 natural sentences" in short
    assert "soft length targets" in short


def test_system_content_places_turn_style_after_long_lived_context() -> None:
    content = _system_content(
        AgentRunContext(
            session_id="s1",
            scope=MemoryScope(user_id="u1"),
            history=[],
            memory_prompt="memory",
            summary="older summary",
            response_style="micro",
        )
    )

    assert "real friend in a phone chat" in content
    assert "Selected response style: MICRO" in content
    assert content.index("older summary") < content.index("Selected response style: MICRO")


def test_proactive_prompt_limits_each_message_to_one_small_idea() -> None:
    assert "only one observation, thought, or question" in PROACTIVE_SYSTEM_PROMPT
    assert "within about 80 Chinese characters" in PROACTIVE_SYSTEM_PROMPT
