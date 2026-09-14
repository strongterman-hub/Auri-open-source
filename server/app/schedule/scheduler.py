import asyncio
import logging
import json
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.proactive.models import (
    ProactiveCategory,
    ProactiveDecision,
    ProactivePhase,
    TriggerType,
)
from app.agent.structured import structured_json
from app.agent.situation import conversation_brief, message_text
from app.core.token_logger import token_context

logger = logging.getLogger("auri.schedule")


class ScheduleScheduler:
    def __init__(
        self, service, delivery, tick_seconds=30, *, llm=None, session_service=None
    ):
        self.service, self.delivery = service, delivery
        self.tick_seconds, self._task = tick_seconds, None
        self.llm, self.session_service = llm, session_service

    async def _already_progressed(self, user, agent, event, now):
        if self.llm is None or self.session_service is None:
            return False
        try:
            sessions = await self.session_service.list_by_user(user)
            active = [
                s for s in sessions if s.agent_id == agent and s.end_reason is None
            ]
            if not active:
                return False
            recent = []
            for m in max(active, key=lambda s: s.updated_at).messages[-60:]:
                at = datetime.fromisoformat(m["timestamp"])
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                if now - timedelta(hours=6) <= at <= now:
                    recent.append(m)
            progress = {
                m["id"]: message_text(m)
                for m in recent
                if m.get("role") == "user"
                and re.search(
                    r"已经|已出发|已到|都出发|弄好了|订好了|做完了", message_text(m)
                )
            }
            if not progress:
                return False
            with token_context(kind="schedule_relevance_check", user_id=user):
                data = await structured_json(
                    self.llm,
                    [
                        {
                            "role": "system",
                            "content": "Decide if this scheduled reminder is redundant because a recent USER explicitly "
                            "acknowledged or made progress on THIS exact event. Only skip with a clear event link "
                            "and no remaining useful action in this reminder. Departure does not complete a meeting. "
                            "Unrelated outings, ambiguous replies and assistant claims cannot justify skipping. "
                            'Return JSON {"skip":bool,"confidence":number,"source_message_id":string}.',
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "event": event,
                                    "context": conversation_brief(
                                        recent, now.isoformat()
                                    ),
                                    "owner_progress": progress,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    max_tokens=2048,
                    validate=lambda d: isinstance(d.get("skip"), bool),
                )
            return (
                data["skip"]
                and float(data.get("confidence", 0)) >= 0.9
                and data.get("source_message_id") in progress
            )
        except Exception:
            return False

    async def tick(self, now=None):
        now = now or datetime.now(timezone.utc)
        fired = []
        for user, agent in self.service.users():
            day = now.astimezone(ZoneInfo(self.service.timezone_resolver(user))).date()
            for event in self.service.list(
                user, agent, day - timedelta(days=32), day + timedelta(days=9)
            )["events"]:
                if event["status"] != "scheduled" or event["reminder_minutes"] is None:
                    continue
                start = datetime.fromisoformat(event["starts_at"])
                due = start.astimezone(timezone.utc) - timedelta(
                    minutes=event["reminder_minutes"]
                )
                if (
                    not (due <= now <= due + timedelta(hours=24))
                    or datetime.fromisoformat(event["ends_at"]) <= now
                ):
                    continue
                key = None
                try:
                    key = self.service.claim(user, agent, event, now)
                    if not key:
                        continue
                    if (
                        self.service.get(user, agent, event["id"])["version"]
                        != event["version"]
                    ):
                        self.service.finish(key, success=False, now=now)
                        continue
                    progressed = await self._already_progressed(user, agent, event, now)
                    if (
                        self.service.get(user, agent, event["id"])["version"]
                        != event["version"]
                    ):
                        self.service.finish(key, success=False, now=now)
                        continue
                    if progressed:
                        self.service.finish(key, success=True, now=now)
                        logger.info(
                            "schedule reminder skipped after owner progress: %s", key
                        )
                        continue
                    when = start.strftime("%m月%d日") + (
                        "（全天）" if event["all_day"] else start.strftime(" %H:%M")
                    )
                    decision = ProactiveDecision(
                        id=key,
                        user_id=user,
                        agent_id=agent,
                        trigger_type=TriggerType.time,
                        should_message=True,
                        phase=ProactivePhase.daily,
                        category=ProactiveCategory.goal_reminder,
                        insight_key=key,
                        message=f"提醒你：{when}有「{event['title']}」。"
                        + (f"地点：{event['location']}。" if event["location"] else ""),
                        importance=8,
                    )
                    await self.delivery.deliver(decision, allow_push=True)
                    self.service.finish(
                        key, success=decision.push_status != "push_failed", now=now
                    )
                    fired.append(key)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    if key:
                        self.service.finish(key, success=False, now=now)
                    logger.exception("schedule reminder delivery failed")
        return fired

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(
                self._run(), name="auri-schedule-scheduler"
            )

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self):
        while True:
            try:
                await self.tick()
            except Exception:
                logger.exception("schedule scheduler tick failed")
            await asyncio.sleep(self.tick_seconds)
