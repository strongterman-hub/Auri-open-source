from __future__ import annotations

from collections.abc import Callable

from app.agent.llm import EchoClient, LLMClient, OpenAICompatibleClient
from app.config import Settings
from app.core.token_logger import TokenLogger


def build_llm_client(
    settings: Settings,
    token_logger: TokenLogger | None = None,
    usage_guard: Callable[[], None] | None = None,
) -> LLMClient:
    if settings.llm_provider == "echo":
        return EchoClient()
    if settings.llm_api_key:
        return OpenAICompatibleClient(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            token_logger=token_logger,
            usage_guard=usage_guard,
        )
    return EchoClient()
