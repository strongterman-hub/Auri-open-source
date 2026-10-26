import asyncio
import json

import pytest

from app.agent.llm import LLMClient, LLMResponse, ToolCall
from app.agent.runner import AgentRunContext, BasicAgentRunner
from app.chat.expression import ExpressionFormatError, parse_expression
from app.core.turn_logger import TurnLogger
from app.memory.models import MemoryScope


PLAN = json.dumps({"auri_reply": 1, "messages": ["你过了？", "太好了！"],
                   "emotion": {"name": "happy", "intensity": .5, "reason": "用户通过考试"},
                   "sticker_id": "female-happy"}, ensure_ascii=False)


@pytest.mark.parametrize("raw", [PLAN, "```JSON\n" + PLAN + "\n```", "```\n" + PLAN + "\n```",
                                 "这是回复计划：\n" + PLAN, "\ufeff" + PLAN])
def test_complete_wrapped_plan_preserves_expression(raw):
    plan = parse_expression(raw, require_plan=True)
    assert plan.messages == ["你过了？", "太好了！"]
    assert plan.emotion["name"] == "happy"
    assert plan.sticker_id == "female-happy"


@pytest.mark.parametrize("raw", ["太好了！", PLAN[:-3], PLAN + PLAN,
                                 '{"auri_reply":true,"messages":["x"]}',
                                 '{"auri_reply":1,"messages":["x"]}'])
def test_expressive_mode_does_not_silently_accept_plain_or_incomplete(raw):
    with pytest.raises(ExpressionFormatError):
        parse_expression(raw, require_plan=True)


def test_repair_never_reexecutes_tools_and_is_audited(tmp_dir):
    class Tool:
        name = "lookup"
        calls = 0
        def to_openai_tool(self):
            return {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}
        async def execute(self):
            self.calls += 1
            return "passed"
    class LLM(LLMClient):
        calls = 0
        repairs = 0
        async def complete(self, messages, tools=None, max_tokens=None, model=None):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(content="", tool_calls=[ToolCall("t", "lookup", {})])
            return LLMResponse(content="你过了？太好了！")
        async def complete_structured(self, messages, *, max_tokens=4096):
            self.repairs += 1
            assert any(m.get("role") == "tool" for m in messages)
            return LLMResponse(content=PLAN)
    tool, llm = Tool(), LLM()
    log = tmp_dir / "turns.jsonl"
    turn = asyncio.run(BasicAgentRunner(llm, turn_logger=TurnLogger(log)).run(AgentRunContext(
        session_id="s", scope=MemoryScope(user_id="u"), history=[], memory_prompt="",
        tools=[tool], expression_prompt="plan")))
    assert tool.calls == 1 and llm.calls == 2 and llm.repairs == 1
    assert turn.expression.messages == ["你过了？", "太好了！"]
    audit = json.loads(log.read_text())["expression"]
    assert audit["status"] == "repaired" and audit["reason"] == "missing_plan"
    assert "太好了" not in log.read_text()


def test_truncated_repair_is_not_delivered(tmp_dir):
    class LLM(LLMClient):
        async def complete(self, *args, **kwargs):
            return LLMResponse(content=PLAN, finish_reason="length")
    log = tmp_dir / "turns.jsonl"
    with pytest.raises(ExpressionFormatError):
        asyncio.run(BasicAgentRunner(LLM(), turn_logger=TurnLogger(log)).run(AgentRunContext(
            session_id="s", scope=MemoryScope(user_id="u"), history=[], memory_prompt="", expression_prompt="plan")))
    assert json.loads(log.read_text())["status"] == "expression_failed"
