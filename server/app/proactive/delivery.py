from __future__ import annotations

from datetime import datetime, timezone

from app.proactive.models import DeliveryChannel, ProactiveDecision
from app.proactive.push import PushSender
from app.services.session_service import SessionService
from app.services.presence_service import PresenceService
from app.session.models import SessionCreate


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProactiveDelivery:
    """Appends a proactive message to the user's continuous chat session."""

    def __init__(
        self,
        session_service: SessionService,
        presence: PresenceService,
        push_sender: PushSender,
        presence_ttl_seconds: int = 180,
    ) -> None:
        self.session_service = session_service
        self.presence = presence
        self.push_sender = push_sender
        self.presence_ttl_seconds = presence_ttl_seconds

    async def deliver(
        self,
        decision: ProactiveDecision,
        *,
        allow_push: bool = True,
    ) -> DeliveryChannel:
        session = await self.session_service.ensure(
            SessionCreate(user_id=decision.user_id, agent_id=decision.agent_id)
        )
        decision.session_id = session.id
        # Reuse the decision id as the persisted message id so the client can
        # deduplicate inbox delivery against the session history.
        content: str | list[dict] = decision.message or ""
        if decision.actions:
            content = [
                {"type": "text", "text": decision.message or ""},
                {"type": "actions", "actions": decision.actions},
            ]
        await self.session_service.append_message(
            session.id,
            {
                "id": decision.id,
                "role": "assistant",
                "content": content,
                "timestamp": _utcnow().isoformat(),
                "proactive": True,
                "decision_id": decision.id,
            },
        )
        decision.chat_persisted_at = _utcnow()

        if not allow_push:
            return DeliveryChannel.chat

        device_tokens = self.presence.device_tokens(
            decision.user_id,
            decision.agent_id,
        )
        if not device_tokens:
            decision.push_status = "no_push_target"
            return DeliveryChannel.chat
        decision.push_attempted_at = _utcnow()
        accepted = await self.push_sender.send(decision, device_tokens)
        decision.push_status = "push_accepted" if accepted else "push_failed"
        return DeliveryChannel.push if accepted else DeliveryChannel.chat
