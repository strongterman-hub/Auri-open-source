from __future__ import annotations

import asyncio

import pytest

from app.agent.llm import LLMClient, LLMResponse
from app.agent.runner import AgentRunContext, BasicAgentRunner, _contains_image
from app.memory.models import MemoryScope
from app.schemas.agent import SendMessageRequest
from app.services.agent_service import _build_user_content


def test_send_message_request_requires_content_or_images() -> None:
    assert SendMessageRequest(content="hi").images == []

    request = SendMessageRequest(images=["data:image/jpeg;base64,AAAA"])
    assert request.content == ""
    assert request.images == ["data:image/jpeg;base64,AAAA"]

    with pytest.raises(ValueError):
        SendMessageRequest()


def test_build_user_content_is_text_only_without_images() -> None:
    assert _build_user_content("hi", None) == "hi"
    assert _build_user_content("hi", []) == "hi"


def test_build_user_content_adds_image_parts() -> None:
    content = _build_user_content(
        "what is in this image?",
        ["data:image/jpeg;base64,AAAA"],
    )
    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "what is in this image?"}
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64,AAAA"},
    }


def test_contains_image() -> None:
    assert _contains_image(
        [
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": "x"}}],
            }
        ]
    ) is True
    assert _contains_image([{"role": "user", "content": "hi"}]) is False


class RecordingLLM(LLMClient):
    def __init__(self) -> None:
        self.models: list[str | None] = []

    async def complete(
        self,
        messages,
        tools=None,
        max_tokens=None,
        model=None,
    ) -> LLMResponse:
        self.models.append(model)
        return LLMResponse(content="ok", usage={})


def _context(history: list[dict]) -> AgentRunContext:
    return AgentRunContext(
        session_id="s1",
        scope=MemoryScope(user_id="u1"),
        history=history,
        memory_prompt="memory",
    )


def test_runner_routes_image_turn_to_vision_model() -> None:
    llm = RecordingLLM()
    runner = BasicAgentRunner(llm, vision_model="deepseek-v4-flash-vision-exp")

    asyncio.run(
        runner.run(
            _context(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}
                        ],
                    }
                ]
            )
        )
    )

    assert llm.models == ["deepseek-v4-flash-vision-exp"]


def test_runner_keeps_default_model_without_image() -> None:
    llm = RecordingLLM()
    runner = BasicAgentRunner(llm, vision_model="deepseek-v4-flash-vision-exp")

    asyncio.run(runner.run(_context([{"role": "user", "content": "hi"}])))

    assert llm.models == [None]
