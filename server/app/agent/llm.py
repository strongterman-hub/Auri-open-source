from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from app.core.token_logger import TokenLogger


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class StreamChunk:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    done: bool = False


class LLMClient(ABC):
    """Provider-neutral interface for the model call layer."""

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Complete a chat turn, optionally returning tool calls."""

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a chat turn. Default falls back to a single-shot completion."""

        response = await self.complete(messages, tools, max_tokens, model=model)
        if response.content:
            yield StreamChunk(content=response.content)
        yield StreamChunk(
            tool_calls=response.tool_calls,
            usage=response.usage,
            done=True,
        )


class OpenAICompatibleClient(LLMClient):
    """Minimal OpenAI-compatible chat completions client."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        token_logger: TokenLogger | None = None,
        usage_guard: Callable[[], None] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.token_logger = token_logger
        self.usage_guard = usage_guard

    def _log_usage(
        self,
        call_type: str,
        model: str,
        usage: dict[str, Any] | None,
        duration_ms: float,
    ) -> None:
        if self.token_logger is not None:
            self.token_logger.log(
                model=model,
                call_type=call_type,
                usage=usage,
                duration_ms=duration_ms,
            )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        if self.usage_guard is not None:
            self.usage_guard()
        start = time.monotonic()
        effective_model = model or self.model
        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        self._log_usage(
            "complete",
            effective_model,
            data.get("usage"),
            (time.monotonic() - start) * 1000,
        )

        message = data["choices"][0]["message"]
        tool_calls: list[ToolCall] = []
        for raw_tool_call in message.get("tool_calls") or []:
            function = raw_tool_call.get("function", {})
            name = function.get("name", "")
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=raw_tool_call.get("id", ""),
                    name=name,
                    arguments=arguments,
                )
            )

        return LLMResponse(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            usage=data.get("usage") or {},
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> AsyncIterator[StreamChunk]:
        if self.usage_guard is not None:
            self.usage_guard()
        start = time.monotonic()
        effective_model = model or self.model
        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        accumulated: dict[int, dict[str, str]] = {}
        usage: dict[str, Any] = {}

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    choice = (obj.get("choices") or [{}])[0]
                    delta = choice.get("delta") or {}
                    content = delta.get("content") or ""

                    if obj.get("usage"):
                        usage = obj["usage"]

                    for raw_tool_call in delta.get("tool_calls") or []:
                        index = raw_tool_call.get("index", 0)
                        slot = accumulated.setdefault(
                            index,
                            {"id": "", "name": "", "arguments": ""},
                        )
                        if raw_tool_call.get("id"):
                            slot["id"] = raw_tool_call["id"]
                        function = raw_tool_call.get("function") or {}
                        if function.get("name"):
                            slot["name"] = function["name"]
                        if function.get("arguments"):
                            slot["arguments"] += function["arguments"]

                    if content:
                        yield StreamChunk(content=content)

        tool_calls: list[ToolCall] = []
        for index in sorted(accumulated):
            slot = accumulated[index]
            try:
                arguments = json.loads(slot["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(
                ToolCall(
                    id=slot["id"],
                    name=slot["name"],
                    arguments=arguments,
                )
            )

        self._log_usage(
            "stream",
            effective_model,
            usage,
            (time.monotonic() - start) * 1000,
        )
        yield StreamChunk(tool_calls=tool_calls, usage=usage, done=True)


class EchoClient(LLMClient):
    """Deterministic local client useful for development and tests."""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        last_user = next(
            (msg.get("content") for msg in reversed(messages) if msg.get("role") == "user"),
            "",
        )
        return LLMResponse(
            content=f"echo: {last_user}",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        )
