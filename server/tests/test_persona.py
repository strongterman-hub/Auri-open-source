from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.agent.llm import LLMResponse
from app.agent.response_style import anchor_response_style
from app.chat.reply_planner import ChatReplyPlanner
from app.config import Settings
from app.memory.models import MemoryScope
from app.persona.models import OpenLoop
from app.persona.service import PersonaService
from app.persona.store import PersonaStore


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self.content = content

    async def complete(self, messages, tools=None, max_tokens=None, model=None):
        return LLMResponse(content=self.content)

    async def stream(self, messages, tools=None, max_tokens=None):
        raise NotImplementedError


def _service(tmp_dir, **overrides) -> PersonaService:
    settings = Settings(data_dir=tmp_dir, persona_user_selection_enabled=True, **overrides)
    store = PersonaStore(tmp_dir / "memory.db")
    return PersonaService(store=store, settings=settings)


def test_persona_presets_load_and_user_selection_overrides(tmp_dir) -> None:
    service = _service(tmp_dir)
    scope = MemoryScope(user_id="u1")
    preset_ids = {item["id"] for item in service.list_presets()}
    assert {"warm_friend", "playful", "calm", "efficient"} <= preset_ids
    assert service.resolve_preset(scope).id == "warm_friend"

    service.set_user_selection(
        scope,
        "calm",
        {"proactive_frequency_preset": "quiet", "question_level": "off"},
    )
    selected = service.resolve_preset(scope)
    assert selected.id == "calm"
    assert selected.proactive_frequency_preset == "quiet"
    assert selected.question_level == "off"
    assert service.daily_limit(scope) == 4


def test_relationship_state_and_open_loop_lifecycle(tmp_dir) -> None:
    service = _service(tmp_dir)
    scope = MemoryScope(user_id="u1")

    service.on_user_message(scope, "叫我阿哲，别叫我哥，少问一点")
    state = service.relationship_state(scope)
    assert state.address == "阿哲"
    assert "少提问" in state.boundaries

    service.on_assistant_message(scope, "你明天有空吗？", source="chat")
    assert service.open_loop_count(scope) == 1
    service.on_user_message(scope, "有空的")
    assert service.open_loop_count(scope) == 0


def test_expired_open_loop_is_not_counted(tmp_dir) -> None:
    store = PersonaStore(tmp_dir / "memory.db")
    scope = MemoryScope(user_id="u1")
    loop = OpenLoop(
        summary="过期的提问",
        asked_at=datetime.now(timezone.utc) - timedelta(hours=2),
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    store.add_open_loop(scope.user_id, scope.agent_id, loop)
    assert store.count_open_loops(scope.user_id, scope.agent_id) == 0
    assert store.expire_open_loops(scope.user_id, scope.agent_id) == 1


def test_anchor_response_style_caps_casual_but_not_tasks() -> None:
    assert anchor_response_style("好的", "normal") == "micro"
    assert anchor_response_style("这是一段普通的闲聊内容", "normal") == "short"
    assert anchor_response_style("帮我分析一下这个方案", "detailed", is_task=True) == "detailed"
    assert anchor_response_style("胸痛怎么办", "short", is_safety=True) == "short"


def test_planner_uses_persona_for_silence_and_caps_casual_length(tmp_dir) -> None:
    service = _service(tmp_dir)
    planner = ChatReplyPlanner(
        _FakeLLM(
            '{"outcome":"reply","lane":"fast","verbosity":"detailed","reason":"test"}'
        ),
        persona_service=service,
    )
    silent = asyncio.run(
        planner.plan(
            [{"role": "user", "content": "好的"}],
            allow_silent=True,
            user_id="u1",
        )
    )
    assert silent.outcome == "silent"
    assert silent.verbosity == "micro"

    casual = asyncio.run(
        planner.plan(
            [{"role": "user", "content": "我们聊聊最近看的电影"}],
            allow_silent=True,
            user_id="u1",
        )
    )
    assert casual.verbosity == "short"
    assert casual.lane == "normal"
    assert casual.allow_question is False
    assert casual.persona_id == "warm_friend"


def test_planner_keeps_task_band_and_attaches_policy(tmp_dir) -> None:
    service = _service(tmp_dir)
    planner = ChatReplyPlanner(
        _FakeLLM(
            '{"outcome":"reply","lane":"fast","verbosity":"detailed","reason":"task"}'
        ),
        persona_service=service,
    )
    plan = asyncio.run(
        planner.plan(
            [{"role": "user", "content": "请详细分析一下这个方案的优缺点"}],
            allow_silent=True,
            user_id="u1",
        )
    )
    assert plan.verbosity == "detailed"
    assert plan.allow_question is True
    assert plan.policy_reason


def test_chat_blocks_include_persona_relationship_and_behavior(tmp_dir) -> None:
    service = _service(tmp_dir)
    scope = MemoryScope(user_id="u1")
    service.on_user_message(scope, "叫我阿哲")
    persona, relationship, behavior = service.build_chat_blocks(
        scope,
        "在吗",
        "short",
        allow_silent=True,
    )
    assert "[PERSONA - ACTIVE PRESET]" in persona
    assert "阿哲" in relationship
    assert "[BEHAVIOR POLICY - THIS TURN]" in behavior


def test_persona_disabled_returns_empty_blocks_and_falls_back(tmp_dir) -> None:
    service = _service(tmp_dir, persona_enabled=False)
    scope = MemoryScope(user_id="u1")
    assert service.is_enabled(scope) is False
    assert service.build_chat_blocks(scope, "你好", "short") == ("", "", "")
    assert service.daily_limit(scope) == 24