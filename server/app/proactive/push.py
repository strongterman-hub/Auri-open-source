from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.proactive.models import ProactiveDecision


class PushSender(ABC):
    """Vendor-neutral system push channel."""

    @abstractmethod
    async def send(
        self,
        decision: ProactiveDecision,
        device_tokens: list[str],
    ) -> bool:
        """Return whether the push provider accepted the request."""


class NullPushSender(PushSender):
    """No-op push sender for local development and environments without a vendor."""

    def __init__(self) -> None:
        self.logger = logging.getLogger("auri.push")

    async def send(
        self,
        decision: ProactiveDecision,
        device_tokens: list[str],
    ) -> bool:
        self.logger.info(
            "push (null) user=%s channel=%s tokens=%d message=%s",
            decision.user_id,
            decision.delivery_channel.value,
            len(device_tokens),
            decision.push_message or decision.message,
        )
        return True
