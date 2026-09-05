import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from app.proactive.models import ProactiveCategory, ProactiveDecision, ProactivePhase, TriggerType

logger = logging.getLogger("auri.schedule")

class ScheduleScheduler:
    def __init__(self, service, delivery, tick_seconds=30):
        self.service, self.delivery = service, delivery
        self.tick_seconds, self._task = tick_seconds, None

    async def tick(self, now=None):
        now = now or datetime.now(timezone.utc)
        fired = []
        for user, agent in self.service.users():
            day = now.astimezone(ZoneInfo(self.service.timezone_resolver(user))).date()
            for event in self.service.list(user, agent, day - timedelta(days=32), day + timedelta(days=9))["events"]:
                if event["status"] != "scheduled" or event["reminder_minutes"] is None:
                    continue
                start = datetime.fromisoformat(event["starts_at"])
                due = start.astimezone(timezone.utc) - timedelta(minutes=event["reminder_minutes"])
                if not (due <= now <= due + timedelta(hours=24)) or datetime.fromisoformat(event["ends_at"]) <= now:
                    continue
                key = None
                try:
                    key = self.service.claim(user, agent, event, now)
                    if not key:
                        continue
                    if self.service.get(user, agent, event["id"])["version"] != event["version"]:
                        self.service.finish(key, success=False, now=now)
                        continue
                    when = start.strftime("%m月%d日") + ("（全天）" if event["all_day"] else start.strftime(" %H:%M"))
                    decision = ProactiveDecision(
                        id=key, user_id=user, agent_id=agent, trigger_type=TriggerType.time,
                        should_message=True, phase=ProactivePhase.daily,
                        category=ProactiveCategory.goal_reminder, insight_key=key,
                        message=f"提醒你：{when}有「{event['title']}」。" + (f"地点：{event['location']}。" if event["location"] else ""), importance=8,
                    )
                    await self.delivery.deliver(decision, allow_push=True)
                    self.service.finish(key, success=decision.push_status != "push_failed", now=now)
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
            self._task = asyncio.create_task(self._run(), name="auri-schedule-scheduler")

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
