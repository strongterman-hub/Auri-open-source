from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.chat.reply_planner import ChatReplyPlanner
from app.chat.reply_store import ChatReplyStore, ReplyJob
from app.proactive.models import ProactiveDecision, TriggerType
from app.proactive.push import PushSender
from app.schemas.agent import FileAttachment
from app.services.agent_service import AgentService
from app.services.presence_service import LocationRequester, PresenceService
from app.services.session_service import SessionService


class ChatReplyService:
    """Accept user messages quickly, then plan and produce replies in the background."""

    def __init__(
        self,
        *,
        store: ChatReplyStore,
        planner: ChatReplyPlanner,
        agent_service: AgentService,
        session_service: SessionService,
        presence: PresenceService,
        push_sender: PushSender,
        debounce_seconds: float = 3.0,
        fast_delay: tuple[float, float] = (1.5, 6.0),
        normal_delay: tuple[float, float] = (8.0, 30.0),
        away_delay: tuple[float, float] = (30.0, 300.0),
        max_attempts: int = 3,
        retry_seconds: float = 15.0,
        audit_path: Path | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.store = store
        self.planner = planner
        self.agent_service = agent_service
        self.session_service = session_service
        self.presence = presence
        self.push_sender = push_sender
        self.debounce_seconds = debounce_seconds
        self.delays = {
            "fast": fast_delay,
            "normal": normal_delay,
            "away": away_delay,
        }
        self.max_attempts = max_attempts
        self.retry_seconds = retry_seconds
        self.audit_path = Path(audit_path) if audit_path else None
        self.rng = rng or random.Random()
        self.logger = logging.getLogger("auri.chat_reply")

    async def accept(
        self,
        session_id: str,
        client_message_id: str,
        content: str,
        images: list[str] | None = None,
        files: list[FileAttachment] | None = None,
    ) -> tuple[Any, dict, str]:
        session, message, duplicate = await self.agent_service.accept_message(
            session_id,
            client_message_id,
            content,
            images,
            files,
        )
        if not duplicate:
            self.store.enqueue(
                session_id=session.id,
                user_id=session.user_id,
                agent_id=session.agent_id,
                message_id=message["id"],
                debounce_seconds=self.debounce_seconds,
            )
        return session, message, self.store.reply_state(session.id)

    async def process_one(self) -> bool:
        job = self.store.claim_due()
        if job is None:
            return False
        if job.status == "planning":
            await self._plan(job)
            return True
        await self._reply(job)
        return True

    async def _plan(self, job: ReplyJob) -> None:
        try:
            session = await self.session_service.get(job.session_id)
            wanted = set(job.message_ids)
            messages = [
                message for message in session.messages if message.get("id") in wanted
            ]
            if not messages:
                raise ValueError("reply batch has no persisted user messages")
            allow_silent = not any(message.get("onboarding_reply") for message in messages)
            plan = await self.planner.plan(
                messages,
                recent_history=session.messages,
                allow_silent=allow_silent,
                user_id=job.user_id,
                session_id=job.session_id,
            )
            if plan.outcome == "silent":
                await self.session_service.settle_messages(
                    job.session_id,
                    job.message_ids,
                    outcome="silent",
                    reply_job_id=job.id,
                )
                self.store.finish_planning(job.id, outcome="silent")
                self._audit(job, "silent", plan.lane, plan.reason)
                return

            low, high = self.delays.get(plan.lane, self.delays["fast"])
            # When a delayed job was reopened to merge a later message, keep its
            # existing friend-availability window instead of adding a second delay.
            delay = 0.0 if job.lane else self.rng.uniform(min(low, high), max(low, high))
            self.store.finish_planning(
                job.id,
                outcome="reply",
                lane=plan.lane,
                delay_seconds=delay,
            )
            self._audit(job, "planned", plan.lane, plan.reason, delay_ms=round(delay * 1000))
        except Exception as exc:  # planner failures must become fast replies, never silence
            self.logger.exception("reply planning failed job=%s", job.id)
            self.store.finish_planning(
                job.id, outcome="reply", lane="fast", delay_seconds=0
            )
            self._audit(job, "planner_fallback", "fast", type(exc).__name__)

    async def _reply(self, job: ReplyJob) -> None:
        async def emit_location_request(request_id: str) -> None:
            self.store.add_location_command(job.session_id, request_id)

        requester = LocationRequester(self.presence, emit_location_request)
        try:
            turn, scope, _session, message = await self.agent_service.reply_to_messages(
                job.session_id,
                job.message_ids,
                reply_job_id=job.id,
                location_requester=requester,
            )
            await self.session_service.settle_messages(
                job.session_id,
                job.message_ids,
                outcome="replied",
                reply_job_id=job.id,
            )
            self.store.complete(job.id)
            decision = ProactiveDecision(
                id=str(message.get("id") or job.id),
                user_id=scope.user_id,
                agent_id=scope.agent_id,
                trigger_type=TriggerType.event,
                should_message=True,
                message=turn.text or "新消息",
                push_message=(turn.text or "新消息")[:180],
            )
            try:
                await self.push_sender.send(
                    decision,
                    self.presence.device_tokens(scope.user_id, scope.agent_id),
                )
            except Exception:
                self.logger.exception("reply push failed job=%s", job.id)
            self._audit(job, "replied", job.lane or "fast", "completed")
        except Exception as exc:
            delay = self.retry_seconds * (2 ** max(0, job.attempt_count))
            status = self.store.retry_or_fail(
                job.id,
                f"{type(exc).__name__}: {exc}",
                max_attempts=self.max_attempts,
                delay_seconds=delay,
            )
            self.logger.exception("background reply failed job=%s status=%s", job.id, status)
            self._audit(job, status, job.lane or "fast", type(exc).__name__)
            return

        # Post-reply onboarding is best-effort and must never reopen a reply
        # job whose assistant message is already durable.
        if self.agent_service.dense_onboarding_hook is not None:
            try:
                await self.agent_service.dense_onboarding_hook(
                    scope.user_id, scope.agent_id
                )
            except Exception:
                self.logger.exception("dense onboarding follow-up failed job=%s", job.id)

    def updates(self, session_id: str) -> tuple[str, list[dict]]:
        return self.store.reply_state(session_id), self.store.pop_commands(session_id)

    def _audit(
        self,
        job: ReplyJob,
        outcome: str,
        lane: str,
        reason: str,
        *,
        delay_ms: int | None = None,
    ) -> None:
        if self.audit_path is None:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "job_id": job.id,
            "session_id": job.session_id,
            "user_id": job.user_id,
            "outcome": outcome,
            "lane": lane,
            "reason": reason,
            "message_count": len(job.message_ids),
            "delay_ms": delay_ms,
        }
        try:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            self.logger.warning("failed to write chat reply audit")
