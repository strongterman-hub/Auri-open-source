from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.agent.llm import LLMClient
from app.agent.tools import Tool
from app.config import Settings
from app.core.token_logger import token_context
from app.memory.intents import IntentStore
from app.memory.models import MemoryScope
from app.memory.told import Told, ToldFeedback, ToldStore
from app.proactive.delivery import ProactiveDelivery
from app.proactive.context import (
    Interruptibility,
    ProactiveAuditLogger,
    ProactiveContextBuilder,
    ProactiveGateLogger,
    SignalFreshness,
    SituationSnapshot,
)
from app.proactive.models import (
    ConversationIntent,
    DeliveryChannel,
    EngagementState,
    ProactiveCategory,
    ProactiveDecision,
    ProactivePhase,
    TriggerType,
)
from app.proactive.pacing import ProactivePacingStore
from app.proactive.preferences import PreferenceStore
from app.proactive.profile import (
    DENSE_SETTLED_TARGET_SLOTS,
    GUIDE_SLOTS,
    ONBOARDING_TIERS,
    PROFILE_SLOTS,
    TOTAL_ONBOARDING_SLOTS,
    ProfileStore,
)
from app.proactive.settings import ProactiveSettingsStore
from app.proactive.store import ProactiveStore
from app.services.memory_service import MemoryService
from app.services.event_memory_service import EventMemoryService
from app.services.observation_service import ObservationService
from app.services.presence_service import PresenceService
from app.services.session_service import SessionService
from app.services.timezone_store import resolve_zoneinfo


PROACTIVE_SYSTEM_PROMPT = (
    "You are Auri, a proactive personal agent. First infer what the user may be "
    "doing from the supplied situation snapshot, then decide whether interrupting "
    "them is appropriate and, only then, what to say. Missing data means unknown, "
    "not safe to guess. Fresh signals may support current-state claims; stale or "
    "expired signals may provide background only. Never treat configured fallback "
    "weather as the user's live location. Respect the deterministic "
    "interruptibility result and all timestamps. You may freely choose any supplied "
    "message category; a useful health observation, weather change, memory, new "
    "idea, or light question can all be appropriate. Return only JSON with keys: "
    "situation_summary, situation_confidence (0..1), evidence_refs (signal ids), "
    "should_message (boolean), decision_reason, silence_reason, category, message, "
    "conversation_intent (share, soft_check_in, or direct_question), and optional "
    "push_message. If should_message is false, message must be empty. "
    "Every concrete claim about the user's present situation must cite one or more "
    "fresh evidence_refs. A planned event is not completed, and an expired current "
    "state is not the user's present situation. Do not invent a shared location or "
    "a causal link between events; when chronology is uncertain, avoid the claim."
    " Keep the user-facing message restrained like one phone-chat message: express "
    "only one observation, thought, or question, normally in one or two short "
    "sentences and within about 80 Chinese characters. Do not turn a check-in into "
    "a report or list. Exceed this soft target only when essential health or safety "
    "information would otherwise be lost. Make push_message even shorter."
)


logger = logging.getLogger("auri.proactive")


PROFILE_SYSTEM_PROMPT = (
    "You are Auri, a general-purpose personal agent on the user's phone. "
    "You can check weather, analyze Xiaomi-band health data (steps, sleep, "
    "heart rate), set reminders and todos, search the web and read links, do "
    "calculations and unit conversions, and remember the user's preferences "
    "over time.\n\n"
    "You are now onboarding a brand-new user. Each turn you decide the single "
    "next step from the open items below and write one short, natural "
    "Simplified Chinese message. Do not follow a fixed order.\n\n"
    "There are two kinds of open items:\n"
    "- profile fields: ask exactly one question to fill it. Ask for the user's "
    "name (how to address them) before introducing capabilities.\n"
    "- guide items: guide the user to complete one concrete action. State "
    "clearly what to do and point to the action (for example, \"点击下方按钮授权\"). "
    "Keep each guide short and actionable. If the provided Xiaomi status says "
    "the user is already connected, do not ask them to connect again. "
    "For capability_intro, write naturally rather than listing features; for "
    "example: \"我会主动关心你的健康、作息和重要安排，在合适的时候轻轻提醒你。"
    "你可以像和朋友聊天一样跟我说话，我会慢慢了解你。\"\n\n"
    "During onboarding you are the guide: introduce yourself and lead the user "
    "through setup. Do not ask open-ended questions such as \"有什么我可以帮你的吗\". "
    "Never ask for the user's country or city. Return only JSON with keys slot "
    "and message."
)


GUIDE_ACTIONS: dict[str, list[dict]] = {
    "capability_intro": [],
    "xiaomi_connect": [{"type": "open_health", "label": "去连接小米手环"}],
    "proactive_enable": [{"type": "enable_proactive", "label": "开启主动消息"}],
    "notification": [{"type": "request_notification", "label": "授权通知"}],
    "location": [{"type": "request_location", "label": "授权位置"}],
    "autostart": [{"type": "open_autostart", "label": "开启自启动"}],
}


GUIDE_MESSAGES: dict[str, str] = {
    "xiaomi_connect": (
        "接下来先把小米手环连上，这样我才能主动关注你的步数、睡眠和心率。"
    ),
    "proactive_enable": (
        "开启主动服务后，我会在合适的时候主动关心你，比如天气变化、健康提醒。"
        "生成主动消息会按实际模型用量扣除 Credits。"
    ),
    "notification": (
        "开启通知后，我才能在需要时及时提醒你，不会错过重要的事情。"
    ),
    "location": (
        "开启位置后，我能更贴心地根据你所在的地方，提供天气和生活提醒。"
    ),
    "autostart": (
        "开启自启动后，我才能稳定地在后台持续关心你。"
    ),
}


ONBOARDING_REPLY_ASSESSMENT_SYSTEM_PROMPT = (
    "You are Auri. Given one profile field and the user's latest message, decide "
    "whether the message actually provides an answer to that field. Return only "
    "JSON with a single key answered, which must be a boolean."
)


REPLY_ASSESSMENT_SYSTEM_PROMPT = (
    "You are Auri. Given one proactive message you sent and the user's latest "
    "message, decide whether the user is actually replying to that proactive "
    "message. Return only JSON with a single key replied, which must be a "
    "boolean. If the user is just starting an unrelated topic, return false."
)


DAILY_CATEGORIES: tuple[ProactiveCategory, ...] = (
    ProactiveCategory.health_insight,
    ProactiveCategory.health_care,
    ProactiveCategory.weather,
    ProactiveCategory.explore,
    ProactiveCategory.goal_reminder,
    ProactiveCategory.memory_recall,
)


CATEGORY_GUIDANCE: dict[ProactiveCategory, str] = {
    ProactiveCategory.health_insight:
        "健康洞察：基于健康数据指出一个趋势或异常，并温和解释。",
    ProactiveCategory.health_care:
        "健康关怀：睡眠差、久坐、活动量低等场景下的轻柔提醒或关心。",
    ProactiveCategory.weather:
        "天气环境：结合天气变化给出对用户有影响的提醒（出门、运动、睡眠）。",
    ProactiveCategory.explore:
        "探索新话题：提出一个用户之前没聊过的、开放的新话题，用问句开启。",
    ProactiveCategory.goal_reminder:
        "目标提醒：结合用户目标或前瞻意图，给出一个温和的推进提醒。",
    ProactiveCategory.memory_recall:
        "记忆回访：回访用户长期记忆里的生日、偏好、重要日期或之前提过的事。",
    ProactiveCategory.trending:
        "热点分享：把一条真实的当日热点事件像朋友聊天一样分享给用户。",
}


# Trigger-source -> per-category multiplicative boost. A source missing here has
# no bias (boost 1.0). This lets health syncs favor health topics, weather
# changes favor weather, and the plain time tick favor exploration/memory.
TRIGGER_CATEGORY_BOOST: dict[str, dict[ProactiveCategory, float]] = {
    "health": {
        ProactiveCategory.health_insight: 3.0,
        ProactiveCategory.health_care: 2.0,
    },
    "weather": {ProactiveCategory.weather: 3.0},
    "time": {
        ProactiveCategory.explore: 1.5,
        ProactiveCategory.memory_recall: 1.2,
    },
    "schedule": {ProactiveCategory.goal_reminder: 2.0},
    "trending": {ProactiveCategory.trending: 3.0},
}


# Human-readable explanation injected into the LLM prompt so it knows exactly
# why this evaluation is happening, not just which category shortlist it got.
TRIGGER_SOURCE_DESCRIPTION: dict[str, str] = {
    "time": "定时主动检查：当前没有特定新事件，适合探索新话题或回访记忆",
    "health": "健康数据刚刚同步更新",
    "weather": "天气刚刚发生变化",
    "schedule": "日程相关事件更新",
    "phone_state": "手机状态事件更新",
    "trending": "有新的热点内容可用",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProactiveEngine:
    """Evaluates whether a user should receive a proactive message."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        memory_service: MemoryService,
        observation_service: ObservationService,
        session_service: SessionService,
        told_store: ToldStore,
        intent_store: IntentStore,
        store: ProactiveStore,
        delivery: ProactiveDelivery,
        settings: Settings,
        presence: PresenceService,
        settings_store: ProactiveSettingsStore,
        preference_store: PreferenceStore,
        profile_store: ProfileStore,
        pacing_store: ProactivePacingStore,
        onboarding_pacing_store: ProactivePacingStore | None = None,
        xiaomi_service: Any = None,
        health_store: Any = None,
        trending_service: Any = None,
        tool_factory: Callable[[MemoryScope], list[Tool]] | None = None,
        timezone_resolver: Callable[[str], str] | None = None,
        event_memory_service: EventMemoryService | None = None,
        context_builder: ProactiveContextBuilder | None = None,
        audit_logger: ProactiveAuditLogger | None = None,
        gate_logger: ProactiveGateLogger | None = None,
        has_pending_chat: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.llm = llm
        self.memory_service = memory_service
        self.observation_service = observation_service
        self.session_service = session_service
        self.told_store = told_store
        self.intent_store = intent_store
        self.store = store
        self.delivery = delivery
        self.settings = settings
        self.presence = presence
        self.settings_store = settings_store
        self.preference_store = preference_store
        self.profile_store = profile_store
        self.pacing_store = pacing_store
        self.onboarding_pacing_store = onboarding_pacing_store or pacing_store
        self.xiaomi_service = xiaomi_service
        self.health_store = health_store
        self.trending_service = trending_service
        self.tool_factory = tool_factory
        self.timezone_resolver = timezone_resolver
        self.event_memory_service = event_memory_service
        self.context_builder = context_builder
        self.audit_logger = audit_logger
        self.gate_logger = gate_logger
        self.has_pending_chat = has_pending_chat
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._dense_evaluating: set[str] = set()

    def _lock_for(self, user_id: str, agent_id: str) -> asyncio.Lock:
        key = f"{agent_id}:{user_id}"
        lock = self._user_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[key] = lock
        return lock

    async def tick(self) -> list[ProactiveDecision]:
        """Run one time-triggered pass, evaluating only due users."""
        if not self.settings.proactive_enabled:
            return []
        decisions: list[ProactiveDecision] = []
        now = _utcnow()
        for user_id, agent_id in await self.session_service.list_users():
            if not self._is_daily_due(user_id, agent_id, now):
                self._audit_gate(user_id, agent_id, "time", "blocked", "not_due")
                continue
            try:
                decision = await self.evaluate_daily(
                    user_id, agent_id, TriggerType.time, "time"
                )
                next_due = now + timedelta(
                    seconds=self._daily_interval_seconds(user_id, agent_id)
                )
                self.pacing_store.record_daily_check(
                    user_id, agent_id, next_due.isoformat()
                )
                if decision is not None and decision.should_message:
                    decisions.append(decision)
            except Exception:  # noqa: BLE001 - isolate each account in the tick
                self._audit_gate(user_id, agent_id, "time", "error", "user_tick_failed")
                logger.exception(
                    "proactive user tick failed user_id=%s agent_id=%s",
                    user_id,
                    agent_id,
                )
        return decisions

    async def tick_onboarding(self) -> list[ProactiveDecision]:
        """Run the slow post-dense onboarding pass."""
        if not self.settings.proactive_enabled:
            return []
        decisions: list[ProactiveDecision] = []
        for user_id, agent_id in await self.session_service.list_users():
            if self.profile_store.phase(user_id, agent_id) != "slow":
                continue
            try:
                decision = await self.evaluate_onboarding(
                    user_id, agent_id, TriggerType.time
                )
                if decision is not None and decision.should_message:
                    decisions.append(decision)
            except Exception:  # noqa: BLE001 - isolate each account in the tick
                logger.exception(
                    "proactive onboarding tick failed user_id=%s agent_id=%s",
                    user_id,
                    agent_id,
                )
        return decisions

    async def maybe_onboard(
        self,
        user_id: str,
        agent_id: str,
    ) -> ProactiveDecision | None:
        """Opportunistically start onboarding when a new user comes online."""
        if not self.settings.proactive_enabled:
            self._audit_gate(
                user_id, agent_id, "event", "blocked", "global_disabled"
            )
            return None
        if self.is_dense_onboarding(user_id, agent_id):
            return await self.evaluate_onboarding(user_id, agent_id, TriggerType.event)
        return None

    async def evaluate_user(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
    ) -> ProactiveDecision | None:
        async with self._lock_for(user_id, agent_id):
            return await self._evaluate_user_locked(user_id, agent_id, trigger_type)

    async def evaluate_daily(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
        trigger_source: str = "time",
    ) -> ProactiveDecision | None:
        """Evaluate a daily proactive message regardless of onboarding phase.

        New users stay in the onboarding loop while still receiving health,
        weather, goal and other daily messages, so the two loops are independent
        instead of strictly sequential.
        """
        async with self._lock_for(user_id, agent_id):
            return await self._evaluate_daily_locked(
                user_id, agent_id, trigger_type, trigger_source
            )

    async def evaluate_onboarding(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
    ) -> ProactiveDecision | None:
        """Evaluate the onboarding profile loop for a cold-start user."""
        async with self._lock_for(user_id, agent_id):
            return await self._evaluate_onboarding_locked(user_id, agent_id, trigger_type)

    async def _evaluate_user_locked(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
    ) -> ProactiveDecision | None:
        # Health, weather, observation and similar external events no longer
        # trigger onboarding. They should only drive the daily proactive loop.
        return await self._evaluate_daily_locked(user_id, agent_id, trigger_type)

    async def _evaluate_daily_locked(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
        trigger_source: str = "time",
    ) -> ProactiveDecision | None:
        if not self.settings.proactive_enabled:
            return None

        # A user message that Auri has accepted but not yet settled owns the
        # conversational turn. Do not interleave an unrelated proactive note.
        if self.has_pending_chat is not None and self.has_pending_chat(user_id, agent_id):
            self._audit_gate(
                user_id, agent_id, trigger_source, "blocked", "pending_chat"
            )
            return None

        if not self.settings_store.is_enabled(user_id, agent_id):
            self._audit_gate(
                user_id, agent_id, trigger_source, "blocked", "user_disabled"
            )
            return None

        if self._is_do_not_disturb(user_id, agent_id):
            self._audit_gate(
                user_id, agent_id, trigger_source, "blocked", "do_not_disturb"
            )
            return None

        return await self._evaluate_daily(
            user_id, agent_id, trigger_type, trigger_source
        )

    async def _evaluate_onboarding_locked(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
    ) -> ProactiveDecision | None:
        self._settle_expired_onboarding_slots(user_id, agent_id)
        self._reconcile_onboarding_guides(user_id, agent_id)
        if self.has_pending_chat is not None and self.has_pending_chat(user_id, agent_id):
            return None
        phase = self.profile_store.phase(user_id, agent_id)
        if phase == "dense":
            return await self._evaluate_dense_onboarding(
                user_id, agent_id, trigger_type
            )
        if phase == "slow":
            return await self._evaluate_slow_onboarding(
                user_id, agent_id, trigger_type
            )
        return None

    async def current_phase(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> ProactivePhase:
        return (
            ProactivePhase.onboarding
            if await self._is_onboarding(user_id, agent_id)
            else ProactivePhase.daily
        )

    async def is_onboarding(self, user_id: str, agent_id: str = "default") -> bool:
        return await self._is_onboarding(user_id, agent_id)

    def onboarding_phase(self, user_id: str, agent_id: str = "default") -> str:
        return self.profile_store.phase(user_id, agent_id)

    def is_dense_onboarding(self, user_id: str, agent_id: str = "default") -> bool:
        return self.onboarding_phase(user_id, agent_id) == "dense"

    async def dense_step_after_reply(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> ProactiveDecision | None:
        """Advance dense onboarding immediately after a user chat reply."""
        if not self.is_dense_onboarding(user_id, agent_id):
            return None
        return await self.evaluate_onboarding(user_id, agent_id, TriggerType.event)

    def dense_onboarding_context(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> str | None:
        """Build a dense-onboarding context block for a passive chat reply."""
        self._settle_expired_onboarding_slots(user_id, agent_id)
        self._reconcile_onboarding_guides(user_id, agent_id)
        if self._transition_onboarding_phase(user_id, agent_id) != "dense":
            return None

        eligible_set = set(self._eligible_onboarding_slots(user_id, agent_id))
        profile_open, guide_open = self._open_tier_items(
            user_id, agent_id, eligible_set
        )
        next_guide = None
        for slot in guide_open:
            actions = GUIDE_ACTIONS.get(slot, [])
            if actions:
                next_guide = (slot, actions[0].get("label"))
                break

        completed = sorted(self.profile_store.completed_slots(user_id, agent_id))
        skipped = sorted(self.profile_store.skipped_slots(user_id, agent_id))
        pending = sorted(self.profile_store.pending_slots(user_id, agent_id))
        deferred = sorted(self.profile_store.deferred_slots(user_id, agent_id))
        lines = [
            "The user is currently in dense onboarding.",
            f"Open profile fields: {', '.join(profile_open) or '无'}",
            f"Open guide items: {', '.join(guide_open) or '无'}",
            f"Next guide action: {next_guide[1] if next_guide else '无'}",
            f"Completed slots: {', '.join(completed) or '无'}",
            f"Pending slots: {', '.join(pending) or '无'}",
            f"Deferred slots: {', '.join(deferred) or '无'}",
            f"Skipped slots: {', '.join(skipped) or '无'}",
            (
                "Reply with only one short acknowledgment. Do not list "
                "capabilities and do not write step-by-step instructions."
            ),
        ]
        return "\n".join(lines)

    def next_onboarding_guide(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> tuple[str, list[dict]] | None:
        """Return the next onboarding action button to attach to a passive reply.

        Profile questions do not have action buttons, so while the first open
        tier still contains profile slots we return ``None``. Once the next open
        item is a guide slot with a fixed action, return that slot and its
        actions so the passive reply can advance setup directly.
        """
        self._settle_expired_onboarding_slots(user_id, agent_id)
        self._reconcile_onboarding_guides(user_id, agent_id)
        if self._transition_onboarding_phase(user_id, agent_id) != "dense":
            return None

        eligible_set = set(self._eligible_onboarding_slots(user_id, agent_id))
        profile_open, guide_open = self._open_tier_items(
            user_id, agent_id, eligible_set
        )
        if profile_open:
            return None

        for slot in guide_open:
            actions = GUIDE_ACTIONS.get(slot, [])
            if actions:
                return slot, actions
        return None

    def mark_onboarding_guide_delivered(
        self,
        user_id: str,
        agent_id: str,
        slot: str,
    ) -> None:
        """Mark a guide slot pending after its action was attached to chat."""
        if slot not in GUIDE_SLOTS:
            return
        self.profile_store.mark_slot_pending(user_id, agent_id, slot)
        self.profile_store.increment_dense_pending(user_id, agent_id)
        self.profile_store.mark_message_sent(user_id, agent_id)

    async def complete_guide_action(
        self,
        user_id: str,
        agent_id: str,
        action_type: str,
    ) -> ProactiveDecision | None:
        """Complete a guide action and immediately advance dense onboarding."""
        for slot, actions in GUIDE_ACTIONS.items():
            if any(action.get("type") == action_type for action in actions):
                self.profile_store.mark_slot_completed(user_id, agent_id, slot)
                break
        self.profile_store.reset_dense_pending(user_id, agent_id)
        self.onboarding_pacing_store.record_reply(user_id, agent_id)
        phase = self._transition_onboarding_phase(user_id, agent_id)
        if phase == "dense":
            return await self._evaluate_dense_onboarding(
                user_id, agent_id, TriggerType.event
            )
        return None

    def _xiaomi_bound(self, user_id: str) -> bool:
        if self.xiaomi_service is None:
            return False
        try:
            return bool(self.xiaomi_service.status(user_id).get("bound"))
        except Exception:
            return False

    def _xiaomi_status_text(self, user_id: str) -> str:
        if self.xiaomi_service is None:
            return "未知"
        try:
            status = self.xiaomi_service.status(user_id)
        except Exception:
            return "未知"
        if not status.get("bound"):
            return "未连接小米手环"
        last_sync = status.get("last_sync_at")
        if isinstance(last_sync, (int, float)) and last_sync:
            last_text = datetime.fromtimestamp(
                float(last_sync) / 1000.0, tz=timezone.utc
            ).isoformat()
        else:
            last_text = "未知"
        types = ", ".join(status.get("available_data_types") or [])
        return (
            "已连接小米手环；最近同步时间 "
            f"{last_text}；可用数据类型：{types or '暂无'}"
        )

    async def _is_onboarding(self, user_id: str, agent_id: str) -> bool:
        if not self.settings.proactive_onboarding_enabled:
            return False
        return self.profile_store.phase(user_id, agent_id) != "done"

    def _transition_onboarding_phase(
        self,
        user_id: str,
        agent_id: str,
    ) -> str:
        completed = self.profile_store.completed_count(user_id, agent_id)
        settled = self.profile_store.settled_count(user_id, agent_id)
        if completed >= TOTAL_ONBOARDING_SLOTS:
            phase = "done"
        elif settled >= DENSE_SETTLED_TARGET_SLOTS:
            phase = "slow"
        else:
            phase = "dense"
        self.profile_store.set_phase(user_id, agent_id, phase)
        return phase

    def _settle_expired_onboarding_slots(
        self,
        user_id: str,
        agent_id: str,
    ) -> set[str]:
        expired = self.profile_store.settle_expired_slots(
            user_id,
            agent_id,
            timeout_seconds=self.settings.proactive_onboarding_pending_timeout_seconds,
        )
        if expired:
            self._transition_onboarding_phase(user_id, agent_id)
        return expired

    def _reconcile_onboarding_guides(self, user_id: str, agent_id: str) -> None:
        """Complete verifiable guide slots from current system state."""
        if self._xiaomi_bound(user_id):
            self.profile_store.mark_slot_completed(user_id, agent_id, "xiaomi_connect")
        if self.settings_store.is_enabled(user_id, agent_id):
            self.profile_store.mark_slot_completed(user_id, agent_id, "proactive_enable")
        if self.presence.device_tokens(user_id, agent_id):
            self.profile_store.mark_slot_completed(user_id, agent_id, "notification")
        if self.presence.location(user_id, agent_id) is not None:
            self.profile_store.mark_slot_completed(user_id, agent_id, "location")

    def _settle_ignored_dense_guides(
        self,
        user_id: str,
        agent_id: str,
    ) -> None:
        """Defer untouched guide actions after the user chose normal chat instead."""
        for slot in self.profile_store.pending_slots(user_id, agent_id):
            if slot in GUIDE_SLOTS:
                self.profile_store.mark_slot_deferred(user_id, agent_id, slot)

    async def _evaluate_dense_onboarding(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
    ) -> ProactiveDecision | None:
        key = f"{user_id}:{agent_id}"
        if key in self._dense_evaluating:
            return None
        self._dense_evaluating.add(key)
        try:
            return await self._evaluate_dense_onboarding_inner(
                user_id, agent_id, trigger_type
            )
        finally:
            self._dense_evaluating.discard(key)

    async def _evaluate_dense_onboarding_inner(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
    ) -> ProactiveDecision | None:
        if self.profile_store.phase(user_id, agent_id) != "dense":
            return None

        phase = self._transition_onboarding_phase(user_id, agent_id)
        if phase != "dense":
            return None

        if self.profile_store.dense_pending_count(user_id, agent_id) >= max(
            3, self.settings.proactive_onboarding_pacing_max_unreplied
        ):
            return None

        eligible_set = set(self._eligible_onboarding_slots(user_id, agent_id))
        profile_open, guide_open = self._open_tier_items(
            user_id, agent_id, eligible_set
        )
        if not profile_open and not guide_open:
            self._transition_onboarding_phase(user_id, agent_id)
            return None

        if "routine" in profile_open:
            inferred_routine = self._infer_routine_from_health(user_id)
            if inferred_routine is not None:
                self.profile_store.mark_slot_completed(
                    user_id, agent_id, "routine"
                )
                decision = ProactiveDecision(
                    user_id=user_id,
                    agent_id=agent_id,
                    trigger_type=trigger_type,
                    should_message=True,
                    phase=ProactivePhase.onboarding,
                    category=None,
                    insight_key="profile_routine",
                    message=(
                        "我看了你的手环睡眠数据，已经先帮你把作息记下来了："
                        f"{inferred_routine}如果你觉得不准，随时告诉我。"
                    ),
                    conversation_intent=ConversationIntent.share,
                )
                self.store.add(decision)
                self.profile_store.mark_message_sent(user_id, agent_id)
                await self._deliver_and_persist(decision)
                self.profile_store.increment_dense_pending(user_id, agent_id)
                self._transition_onboarding_phase(user_id, agent_id)
                return decision

        if profile_open:
            first_contact = (
                self.profile_store.onboarding_state(user_id, agent_id)[1] == 0
                and not self.profile_store.completed_slots(user_id, agent_id)
            )
            plan = await self._plan_onboarding(
                user_id,
                agent_id,
                profile_open,
                [],
                first_contact=first_contact,
            )
            slot = plan.get("slot") if isinstance(plan.get("slot"), str) else None
            message = str(plan.get("message") or "").strip()
            if not message or slot not in profile_open:
                return None
            decision = ProactiveDecision(
                user_id=user_id,
                agent_id=agent_id,
                trigger_type=trigger_type,
                should_message=True,
                phase=ProactivePhase.onboarding,
                category=ProactiveCategory.profile_question,
                insight_key=f"profile_{slot}",
                message=message,
                conversation_intent=ConversationIntent.direct_question,
            )
            self.store.add(decision)
            self.profile_store.record_ask(user_id, agent_id, slot)
            self.profile_store.mark_message_sent(user_id, agent_id)
            await self._deliver_and_persist(decision)
            self.profile_store.increment_dense_pending(user_id, agent_id)
            return decision

        if guide_open:
            slot = guide_open[0]
            if slot == "capability_intro":
                plan = await self._plan_onboarding(
                    user_id,
                    agent_id,
                    [],
                    ["capability_intro"],
                    first_contact=False,
                )
                message = str(plan.get("message") or "").strip()
                if not message or plan.get("slot") != "capability_intro":
                    return None
            else:
                message = GUIDE_MESSAGES.get(slot, "")
            decision = self._onboarding_guidance_decision(
                user_id=user_id,
                agent_id=agent_id,
                trigger_type=trigger_type,
                insight_key=slot,
                message=message,
                actions=GUIDE_ACTIONS.get(slot, []),
            )
            self.store.add(decision)
            if GUIDE_ACTIONS.get(slot):
                self.profile_store.mark_slot_pending(user_id, agent_id, slot)
            else:
                self.profile_store.mark_slot_completed(user_id, agent_id, slot)
            self.profile_store.mark_message_sent(user_id, agent_id)
            await self._deliver_and_persist(decision)
            self.profile_store.increment_dense_pending(user_id, agent_id)
            return decision

        return None

    async def _evaluate_slow_onboarding(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
    ) -> ProactiveDecision | None:
        if self.profile_store.phase(user_id, agent_id) != "slow":
            return None

        if not self.settings.proactive_enabled:
            return None
        if not self.settings_store.is_enabled(user_id, agent_id):
            return None
        if self._is_do_not_disturb(user_id, agent_id):
            return None
        if self._within_onboarding_cooldown(user_id, agent_id):
            return None
        if self._is_onboarding_pacing_blocked(user_id, agent_id):
            return None

        eligible_set = set(self._eligible_onboarding_slots(user_id, agent_id))
        profile_open, guide_open = self._open_tier_items(
            user_id, agent_id, eligible_set
        )
        if not profile_open and not guide_open:
            self._transition_onboarding_phase(user_id, agent_id)
            return None

        if profile_open:
            plan = await self._plan_onboarding(
                user_id,
                agent_id,
                profile_open,
                [],
                first_contact=False,
            )
            slot = plan.get("slot") if isinstance(plan.get("slot"), str) else None
            message = str(plan.get("message") or "").strip()
            if not message or slot not in profile_open:
                return None
            decision = ProactiveDecision(
                user_id=user_id,
                agent_id=agent_id,
                trigger_type=trigger_type,
                should_message=True,
                phase=ProactivePhase.onboarding,
                category=ProactiveCategory.profile_question,
                insight_key=f"profile_{slot}",
                message=message,
                conversation_intent=ConversationIntent.direct_question,
            )
            self.store.add(decision)
            self.profile_store.record_ask(user_id, agent_id, slot)
            self.profile_store.mark_message_sent(user_id, agent_id)
            await self._deliver_and_persist(decision)
            self.onboarding_pacing_store.record_sent(user_id, agent_id)
            return decision

        if guide_open:
            slot = guide_open[0]
            if slot == "capability_intro":
                plan = await self._plan_onboarding(
                    user_id,
                    agent_id,
                    [],
                    ["capability_intro"],
                    first_contact=False,
                )
                message = str(plan.get("message") or "").strip()
                if not message or plan.get("slot") != "capability_intro":
                    return None
            else:
                message = GUIDE_MESSAGES.get(slot, "")
            decision = self._onboarding_guidance_decision(
                user_id=user_id,
                agent_id=agent_id,
                trigger_type=trigger_type,
                insight_key=slot,
                message=message,
                actions=GUIDE_ACTIONS.get(slot, []),
            )
            self.store.add(decision)
            if GUIDE_ACTIONS.get(slot):
                self.profile_store.mark_slot_pending(user_id, agent_id, slot)
            else:
                self.profile_store.mark_slot_completed(user_id, agent_id, slot)
            self.profile_store.mark_message_sent(user_id, agent_id)
            await self._deliver_and_persist(decision)
            self.onboarding_pacing_store.record_sent(user_id, agent_id)
            return decision

        return None

    def _open_tier_items(
        self,
        user_id: str,
        agent_id: str,
        eligible_set: set[str],
    ) -> tuple[list[str], list[str]]:
        completed = self.profile_store.completed_slots(user_id, agent_id)
        skipped = self.profile_store.skipped_slots(user_id, agent_id)
        pending = self.profile_store.pending_slots(user_id, agent_id)
        deferred = self.profile_store.deferred_slots(user_id, agent_id)
        is_dense = self.profile_store.phase(user_id, agent_id) == "dense"
        pending_profile = any(slot in PROFILE_SLOTS for slot in pending)
        for tier in ONBOARDING_TIERS:
            profile_open = [
                slot
                for slot in tier
                if slot in PROFILE_SLOTS
                and slot not in completed
                and slot not in skipped
                and not (is_dense and slot in pending | deferred)
                and not (is_dense and pending_profile)
                and (
                    is_dense
                    or self.profile_store.deferred_revisit_allowed(
                        user_id,
                        agent_id,
                        slot,
                        retry_seconds=(
                            self.settings.proactive_onboarding_deferred_retry_seconds
                        ),
                    )
                )
                and slot in eligible_set
            ]
            guide_open = []
            for slot in tier:
                if slot not in GUIDE_SLOTS or slot in completed:
                    continue
                if slot == "xiaomi_connect" and self._xiaomi_bound(user_id):
                    self.profile_store.mark_slot_completed(
                        user_id, agent_id, slot
                    )
                    continue
                if is_dense and (slot in pending or slot in skipped or slot in deferred):
                    continue
                if not is_dense and not self.profile_store.deferred_revisit_allowed(
                    user_id,
                    agent_id,
                    slot,
                    retry_seconds=self.settings.proactive_onboarding_deferred_retry_seconds,
                ):
                    continue
                guide_open.append(slot)
            if profile_open or guide_open:
                return profile_open, guide_open
        return [], []

    def _onboarding_guidance_decision(
        self,
        *,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
        insight_key: str,
        message: str,
        actions: list[dict],
    ) -> ProactiveDecision:
        return ProactiveDecision(
            user_id=user_id,
            agent_id=agent_id,
            trigger_type=trigger_type,
            should_message=True,
            phase=ProactivePhase.onboarding,
            category=None,
            insight_key=insight_key,
            message=message,
            actions=actions,
            conversation_intent=ConversationIntent.share,
        )

    async def _deliver_and_persist(
        self,
        decision: ProactiveDecision,
        *,
        allow_push: bool = True,
    ) -> None:
        decision.delivery_channel = await self.delivery.deliver(
            decision, allow_push=allow_push
        )
        if decision.conversation_intent is ConversationIntent.share:
            decision.engagement_state = EngagementState.no_reply_expected
            decision.settled_at = decision.chat_persisted_at or _utcnow()
        else:
            decision.engagement_state = EngagementState.chat_persisted
        self.store.add(decision)

    async def _evaluate_daily(
        self,
        user_id: str,
        agent_id: str,
        trigger_type: TriggerType,
        trigger_source: str = "time",
    ) -> ProactiveDecision | None:
        self._settle_daily_opportunities(user_id, agent_id)
        if self._is_pacing_blocked(user_id, agent_id):
            self._audit_gate(
                user_id,
                agent_id,
                trigger_source,
                "blocked",
                self._pacing_gate_reason(user_id, agent_id),
            )
            return None
        is_rest_probe = self._is_resting_probe_due(user_id, agent_id)

        scope = MemoryScope(user_id=user_id, agent_id=agent_id)
        observations = self.observation_service.query(
            user_id=user_id,
            agent_id=agent_id,
            limit=10,
        )
        situation_snapshot: SituationSnapshot | None = None
        if self.context_builder is not None:
            try:
                situation_snapshot = await self.context_builder.build(
                    scope,
                    trigger_source=trigger_source,
                )
            except Exception:
                # Context enrichment is best-effort. A broken source must not stop
                # every proactive evaluation; the model will see that it is absent.
                situation_snapshot = None

        if (
            situation_snapshot is not None
            and situation_snapshot.interruptibility
            is Interruptibility.do_not_interrupt
        ):
            reason = (
                "；".join(situation_snapshot.interruptibility_reasons)
                or "情境信号显示当前不适合打扰"
            )
            decision = ProactiveDecision(
                user_id=user_id,
                agent_id=agent_id,
                trigger_type=trigger_type,
                trigger_source=trigger_source,
                should_message=False,
                phase=ProactivePhase.daily,
                context_snapshot_id=situation_snapshot.id,
                situation_summary=reason,
                situation_confidence=1.0,
                evidence_refs=situation_snapshot.interruptibility_evidence,
                decision_reason="确定性可打扰性规则阻止本次主动问询",
                silence_reason=reason,
            )
            self.store.add(decision)
            if is_rest_probe:
                self.pacing_store.finish_probe(user_id, agent_id)
            self._audit_gate(
                user_id, agent_id, trigger_source, "silent", "context_do_not_interrupt"
            )
            self._audit_situation(situation_snapshot, decision)
            return decision

        snapshot = await self.memory_service.snapshot(scope)
        trending_items: list[Any] = []
        if self.trending_service is not None and self.settings.trending_enabled:
            trending_items = await self.trending_service.items()

        forced = self._forced_test_message(observations)
        if forced is not None:
            decision = ProactiveDecision(
                user_id=user_id,
                agent_id=agent_id,
                trigger_type=trigger_type,
                trigger_source=trigger_source,
                should_message=True,
                phase=ProactivePhase.daily,
                category=forced["category"],
                insight_key=forced["insight_key"],
                message=forced["message"],
                context_snapshot_id=(
                    situation_snapshot.id if situation_snapshot else None
                ),
                situation_summary="端到端测试指定消息",
                situation_confidence=1.0,
                decision_reason="显式端到端测试钩子",
                conversation_intent=ConversationIntent.share,
            )
            self.store.add(decision)
            self.told_store.save(
                Told(
                    user_id=user_id,
                    agent_id=agent_id,
                    insight_key=forced["insight_key"],
                    category=forced["category"].value,
                )
            )
            self.preference_store.record_sent(
                user_id, agent_id, decision.category.value
            )
            self.pacing_store.record_sent(
                user_id,
                agent_id,
                conversation_intent=decision.conversation_intent.value,
            )
            await self._deliver_and_persist(decision, allow_push=True)
            if is_rest_probe:
                self.pacing_store.finish_probe(user_id, agent_id)
            self._audit_gate(user_id, agent_id, trigger_source, "sent", "forced_test")
            self._audit_situation(situation_snapshot, decision)
            return decision

        categories = self._available_categories(
            user_id, agent_id, trigger_source, trending_items
        )
        decision_data = await self._decide(
            scope,
            snapshot,
            observations,
            categories,
            trending_items,
            trigger_source,
            situation_snapshot,
        )
        category = decision_data["category"]
        insight_key = decision_data["insight_key"]

        if insight_key and self._insight_was_unhelpful(
            insight_key,
            user_id,
            agent_id,
        ):
            decision_data["should_message"] = False

        decision = ProactiveDecision(
            user_id=user_id,
            agent_id=agent_id,
            trigger_type=trigger_type,
            trigger_source=trigger_source,
            should_message=decision_data["should_message"],
            phase=ProactivePhase.daily,
            category=category,
            insight_key=insight_key,
            message=decision_data.get("message"),
            push_message=decision_data.get("push_message"),
            context_snapshot_id=decision_data.get("context_snapshot_id"),
            situation_summary=decision_data.get("situation_summary"),
            situation_confidence=decision_data.get("situation_confidence"),
            evidence_refs=decision_data.get("evidence_refs", []),
            decision_reason=decision_data.get("decision_reason"),
            silence_reason=decision_data.get("silence_reason"),
            tool_calls=decision_data.get("tool_calls", []),
            conversation_intent=decision_data.get(
                "conversation_intent", ConversationIntent.share
            ),
        )
        if decision.should_message and self._would_exceed_outstanding(
            user_id, agent_id, decision.conversation_intent
        ):
            decision.should_message = False
            decision.message = None
            decision.push_message = None
            decision.silence_reason = "已有等待回复的主动消息，避免连续追问"
        self.store.add(decision)

        if decision.should_message and decision.message:
            self.preference_store.record_sent(user_id, agent_id, category.value)
            self.pacing_store.record_sent(
                user_id,
                agent_id,
                conversation_intent=decision.conversation_intent.value,
            )
            self.told_store.save(
                Told(
                    user_id=user_id,
                    agent_id=agent_id,
                    insight_key=insight_key,
                    category=category.value,
                )
            )
            await self._deliver_and_persist(decision)

        if is_rest_probe:
            self.pacing_store.finish_probe(user_id, agent_id)
        self._audit_gate(
            user_id,
            agent_id,
            trigger_source,
            "sent" if decision.should_message else "silent",
            "delivered" if decision.should_message else "llm_silent",
        )
        self._audit_situation(situation_snapshot, decision)
        return decision

    async def record_reply(
        self,
        user_id: str,
        agent_id: str,
        message_id: str,
        user_message: str | None = None,
    ) -> None:
        """Record that the user replied to a specific proactive message.

        ``user_message`` is optional for callers that only have the message id.
        When it is provided for an onboarding profile question, the engine first
        checks whether the reply actually answers the asked field before marking
        that profile slot filled.
        """
        async with self._lock_for(user_id, agent_id):
            decision = self.store.get(user_id, agent_id, message_id)
            if decision is None:
                return
            if self.is_dense_onboarding(user_id, agent_id):
                self._settle_ignored_dense_guides(user_id, agent_id)
                self.profile_store.reset_dense_pending(user_id, agent_id)
            await self._apply_reply(decision, user_id, agent_id, user_message)

    async def record_user_activity(
        self,
        user_id: str,
        agent_id: str,
        user_message: str,
        proactive_id: str | None = None,
    ) -> None:
        """Apply explicit relationship preference and restart-safe reply attribution."""
        if self.settings.proactive_onboarding_enabled:
            self._settle_expired_onboarding_slots(user_id, agent_id)
            self._reconcile_onboarding_guides(user_id, agent_id)
        preference = self._relationship_preference(user_message)
        if preference == "turn_off":
            self.settings_store.set_enabled(user_id, agent_id, False)
            self._audit_gate(
                user_id, agent_id, "chat", "preference", "turn_off"
            )
        elif preference in {"want_more", "want_less"}:
            self.pacing_store.apply_preference(
                user_id, agent_id, preference=preference
            )
            self._audit_gate(
                user_id, agent_id, "chat", "preference", preference
            )
        elif preference == "quiet_today":
            local_now = datetime.now(
                resolve_zoneinfo(self._user_timezone(user_id))
            )
            quiet_until = local_now.replace(
                hour=8, minute=0, second=0, microsecond=0
            )
            if quiet_until <= local_now:
                quiet_until += timedelta(days=1)
            self.pacing_store.set_temporary_quiet(
                user_id,
                agent_id,
                until=quiet_until.astimezone(timezone.utc),
            )
            self._audit_gate(
                user_id, agent_id, "chat", "preference", "quiet_until_morning"
            )

        if proactive_id:
            await self.record_reply(
                user_id, agent_id, proactive_id, user_message
            )
            return

        since = _utcnow() - timedelta(
            seconds=max(0, self.settings.proactive_reply_candidate_seconds)
        )
        for candidate in self.store.recent_unsettled(
            user_id, agent_id, since=since, limit=3
        ):
            content = await self._decision_message(candidate)
            if not content:
                continue
            if await self._did_reply_to_proactive(user_id, content, user_message):
                self._mark_replied(candidate, user_id, agent_id)
                break

    @staticmethod
    def _relationship_preference(user_message: str) -> str | None:
        text = re.sub(r"\s+", "", user_message or "")
        if any(
            phrase in text
            for phrase in (
                "关闭主动消息",
                "以后别主动发",
                "不要再主动发",
                "别再主动找我",
            )
        ):
            return "turn_off"
        if any(
            phrase in text
            for phrase in (
                "少发一点",
                "少给我发",
                "少找我",
                "别这么频繁",
            )
        ):
            return "want_less"
        if any(
            phrase in text
            for phrase in (
                "今晚别打扰",
                "今晚不要打扰",
                "今天别找我",
                "今天不要找我",
            )
        ):
            return "quiet_today"
        if any(
            phrase in text
            for phrase in (
                "多找我",
                "多给我发",
                "主动一点",
                "怎么没找我",
                "怎么没有找我",
                "还没有给我发",
                "没有给我发过消息",
                "别总等我开口",
            )
        ):
            return "want_more"
        return None

    async def _decision_message(self, decision: ProactiveDecision) -> str:
        if decision.message:
            return decision.message
        if not decision.session_id:
            return ""
        message = await self.session_service.message_by_id(
            decision.session_id, decision.id
        )
        return self._message_text(message.get("content")) if message else ""

    async def _apply_reply(
        self,
        decision: ProactiveDecision,
        user_id: str,
        agent_id: str,
        user_message: str | None,
    ) -> None:
        if (
            decision.phase is ProactivePhase.onboarding
            and decision.category is ProactiveCategory.profile_question
            and decision.insight_key
        ):
            slot = decision.insight_key.removeprefix("profile_")
            if slot in PROFILE_SLOTS:
                answered = True
                if user_message is not None:
                    answered = await self._did_answer_onboarding_question(
                        user_id,
                        slot,
                        user_message,
                    )
                if answered:
                    self.profile_store.mark_slot_completed(
                        user_id, agent_id, slot
                    )
                    self._mark_replied(decision, user_id, agent_id)
                return

        if decision.phase is ProactivePhase.daily:
            if user_message is not None and decision.message:
                replied = await self._did_reply_to_proactive(
                    user_id,
                    decision.message,
                    user_message,
                )
                if not replied:
                    return

        self._mark_replied(decision, user_id, agent_id)

    def _mark_replied(
        self,
        decision: ProactiveDecision,
        user_id: str,
        agent_id: str,
    ) -> None:
        if decision.replied:
            return
        decision.replied = True
        decision.replied_at = _utcnow()
        decision.settled_at = decision.replied_at
        decision.engagement_state = EngagementState.replied
        if decision.category is not None:
            self.preference_store.record_reply(
                user_id, agent_id, decision.category.value
            )
        if decision.insight_key:
            self.told_store.mark_replied(
                decision.insight_key, user_id, agent_id
            )
        if decision.phase is ProactivePhase.daily:
            self.pacing_store.record_reply(
                user_id,
                agent_id,
                count_opportunity=(
                    decision.conversation_intent is not ConversationIntent.share
                ),
            )
        if (
            decision.phase is ProactivePhase.onboarding
            and decision.category is ProactiveCategory.profile_question
        ):
            self.onboarding_pacing_store.record_reply(user_id, agent_id)
        self.store.add(decision)

    async def _did_answer_onboarding_question(
        self,
        user_id: str,
        slot: str,
        user_message: str,
    ) -> bool:
        messages = [
            {"role": "system", "content": ONBOARDING_REPLY_ASSESSMENT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Profile field: {slot}\n"
                    f"User message:\n{user_message}"
                ),
            },
        ]
        with token_context(kind="proactive_onboarding_reply", user_id=user_id):
            response = await self.llm.complete(
                messages,
                tools=None,
                max_tokens=self.settings.proactive_reply_assess_max_tokens,
            )
        data = self._extract_json(response.content)
        if isinstance(data, dict):
            answered = data.get("answered")
            return answered is True
        return False

    async def _did_reply_to_proactive(
        self,
        user_id: str,
        proactive_message: str,
        user_message: str,
    ) -> bool:
        messages = [
            {"role": "system", "content": REPLY_ASSESSMENT_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Proactive message:\n{proactive_message}\n\n"
                    f"User message:\n{user_message}"
                ),
            },
        ]
        with token_context(kind="proactive_reply_assess", user_id=user_id):
            response = await self.llm.complete(
                messages,
                tools=None,
                max_tokens=self.settings.proactive_reply_assess_max_tokens,
            )
        data = self._extract_json(response.content)
        if isinstance(data, dict):
            replied = data.get("replied")
            return replied is True
        return False

    def _available_categories(
        self,
        user_id: str,
        agent_id: str,
        trigger_source: str,
        trending_items: list[Any],
    ) -> list[ProactiveCategory]:
        """Return every daily category the LLM is allowed to pick freely."""
        pool = list(DAILY_CATEGORIES)
        if trending_items and self.settings.trending_enabled:
            pool.append(ProactiveCategory.trending)
        if not pool:
            return [ProactiveCategory.health_insight]
        return pool

    def _eligible_onboarding_slots(
        self,
        user_id: str,
        agent_id: str,
    ) -> list[str]:
        completed = self.profile_store.completed_slots(user_id, agent_id)
        skipped = self.profile_store.skipped_slots(user_id, agent_id)
        return [
            slot
            for slot in PROFILE_SLOTS
            if slot not in completed and slot not in skipped
        ]

    def _within_onboarding_cooldown(
        self,
        user_id: str,
        agent_id: str,
    ) -> bool:
        last = self.profile_store.last_onboarding_at(user_id, agent_id)
        if last is None:
            return False
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        cooldown_seconds = self.settings.proactive_onboarding_cooldown_seconds
        if self.profile_store.phase(user_id, agent_id) == "slow":
            cooldown_seconds = self.settings.proactive_onboarding_slow_cooldown_seconds
        return (
            (_utcnow() - last).total_seconds()
            < cooldown_seconds
        )

    def _pending_slot_expired(
        self,
        user_id: str,
        agent_id: str,
        slot: str,
    ) -> bool:
        """Return True when a pending question has waited long enough to move on.

        A pending slot normally waits for the user's reply, but a misjudged reply
        assessment can leave it stuck forever. After the configured timeout we
        treat the question as unanswered and let onboarding ask the next field.
        """
        asked_at = self.profile_store.slot_last_asked_at(user_id, agent_id, slot)
        if asked_at is None:
            return True
        if asked_at.tzinfo is None:
            asked_at = asked_at.replace(tzinfo=timezone.utc)
        timeout = self.settings.proactive_onboarding_pending_timeout_seconds
        return (_utcnow() - asked_at).total_seconds() >= timeout

    @staticmethod
    def _forced_test_message(observations: list[Any]) -> dict[str, Any] | None:
        """Small explicit hook used only for end-to-end link verification.

        When the latest observation payload contains ``force_proactive_message``,
        the engine skips the LLM decision and sends that exact message. This keeps
        the normal production path unchanged for all other observations.
        """
        if not observations:
            return None
        payload = getattr(observations[0], "payload", None) or {}
        message = payload.get("force_proactive_message")
        if not isinstance(message, str) or not message.strip():
            return None
        insight_key = str(
            payload.get("force_proactive_insight_key") or "forced_test"
        )
        category_raw = payload.get("force_proactive_category")
        try:
            category = (
                ProactiveCategory(category_raw)
                if category_raw
                else ProactiveCategory.health_insight
            )
        except ValueError:
            category = ProactiveCategory.health_insight
        return {
            "insight_key": insight_key,
            "message": message.strip(),
            "category": category,
        }

    async def _latest_active_session_async(
        self,
        user_id: str,
        agent_id: str,
    ) -> Any | None:
        sessions = await self.session_service.list_by_user(user_id)
        active = [
            session
            for session in sessions
            if session.agent_id == agent_id and session.end_reason is None
        ]
        if not active:
            return None
        return max(active, key=lambda session: session.updated_at)

    async def _recent_conversation_text(
        self,
        user_id: str,
        agent_id: str,
        limit: int = 6,
    ) -> str:
        session = await self._latest_active_session_async(user_id, agent_id)
        if session is None:
            return "No active conversation."
        lines: list[str] = []
        for message in session.messages[-limit:]:
            text = self._message_text(message.get("content"))
            if not text:
                continue
            role = message.get("role")
            label = "用户" if role == "user" else "Auri"
            message_time = self._parse_timestamp(message.get("timestamp"))
            if message_time is not None:
                message_time = message_time.astimezone(
                    resolve_zoneinfo(self._user_timezone(user_id))
                )
                timestamp = message_time.isoformat()
            else:
                timestamp = "time unknown"
            lines.append(f"[{timestamp}] {label}: {text}")
        return "\n".join(lines) or "No active conversation."

    @staticmethod
    def _message_text(content: str | list[dict]) -> str:
        if isinstance(content, str):
            return content
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "text":
                parts.append(str(part.get("text") or ""))
            elif kind == "image_url":
                parts.append("[图片]")
            elif kind == "file":
                file_info = part.get("file") or {}
                parts.append(f"[文件：{file_info.get('name', 'file')}]")
        return "\n".join(parts)

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _within_quiet_hours(self, now: datetime | None = None) -> bool:
        window = self.settings.proactive_quiet_hours.strip()
        if not window:
            return False
        parts = window.split("-")
        if len(parts) != 2:
            return False
        try:
            start = int(parts[0])
            end = int(parts[1])
        except ValueError:
            return False
        if not (0 <= start <= 23 and 0 <= end <= 23):
            return False

        if now is not None:
            current = now
        else:
            try:
                current = datetime.now(ZoneInfo(self.settings.proactive_quiet_hours_tz))
            except ZoneInfoNotFoundError:
                current = datetime.now()
        hour = current.hour
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end

    def _insight_was_unhelpful(
        self,
        insight_key: str,
        user_id: str,
        agent_id: str,
    ) -> bool:
        entry = self.told_store.get(insight_key, user_id, agent_id)
        return entry is not None and entry.user_feedback is ToldFeedback.unhelpful

    def _within_cooldown(self, user_id: str, agent_id: str) -> bool:
        told = self.told_store.list(user_id, agent_id)
        if not told:
            return False
        latest = max(entry.told_at for entry in told)
        if latest.tzinfo is None:
            latest = latest.replace(tzinfo=timezone.utc)
        return (_utcnow() - latest).total_seconds() < self.settings.proactive_cooldown_seconds

    def _is_pacing_blocked(self, user_id: str, agent_id: str) -> bool:
        """Decide whether another daily message is due under adaptive pacing."""
        if not self.settings.proactive_pacing_enabled:
            return self._within_cooldown(user_id, agent_id)
        state = self.pacing_store.get_state(user_id, agent_id)
        if state is None:
            return False
        now = _utcnow()
        if state.get("mode") == "temporary_quiet":
            quiet_until = self._parse_timestamp(state.get("quiet_until"))
            if quiet_until is not None and now < quiet_until:
                return True
            self.pacing_store.clear_expired_quiet(user_id, agent_id, now=now)
            state = self.pacing_store.get_state(user_id, agent_id) or state
        miss_weight = float(state.get("miss_weight") or 0.0)
        if state.get("mode") != "resting" and miss_weight >= self._effective_max_unreplied(
            state
        ):
            self.pacing_store.enter_resting(user_id, agent_id, now=now)
            state = self.pacing_store.get_state(user_id, agent_id) or state
        if state.get("mode") == "resting":
            due = self._parse_timestamp(state.get("next_probe_at"))
            return due is not None and now < due
        last_sent_at = self._parse_timestamp(state.get("last_sent_at"))
        if last_sent_at is None:
            return False
        cooldown = self._effective_cooldown_seconds(state)
        return (now - last_sent_at).total_seconds() < cooldown

    def _pacing_gate_reason(self, user_id: str, agent_id: str) -> str:
        state = self.pacing_store.get_state(user_id, agent_id) or {}
        if state.get("mode") == "resting":
            return "resting_until_probe"
        if state.get("mode") == "temporary_quiet":
            return "temporary_quiet"
        return "adaptive_cooldown"

    def _is_resting_probe_due(self, user_id: str, agent_id: str) -> bool:
        state = self.pacing_store.get_state(user_id, agent_id) or {}
        if state.get("mode") != "resting":
            return False
        due = self._parse_timestamp(state.get("next_probe_at"))
        return due is None or _utcnow() >= due

    def _settle_daily_opportunities(self, user_id: str, agent_id: str) -> None:
        before = _utcnow() - timedelta(
            seconds=max(0, self.settings.proactive_engagement_settlement_seconds)
        )
        for _decision, weight in self.store.settle_expired(
            user_id, agent_id, before=before
        ):
            self.pacing_store.record_settlement(
                user_id, agent_id, miss_weight=weight
            )
            if weight > 0:
                state = self.pacing_store.get_state(user_id, agent_id) or {}
                if state.get("mode") == "resting":
                    self.pacing_store.advance_rest_after_miss(user_id, agent_id)

    def _would_exceed_outstanding(
        self,
        user_id: str,
        agent_id: str,
        intent: ConversationIntent,
    ) -> bool:
        if intent is ConversationIntent.share:
            return False
        direct, interactive = self.store.outstanding_counts(user_id, agent_id)
        if interactive >= 2:
            return True
        return intent is ConversationIntent.direct_question and direct >= 1

    def _is_onboarding_pacing_blocked(
        self,
        user_id: str,
        agent_id: str,
    ) -> bool:
        """Stop new onboarding profile questions after too many go unanswered.

        Onboarding already has its own short fixed cooldown, so this only
        applies a fixed unanswered cap and a silent-window cutoff. Guide action
        messages are one-shot prompts and are intentionally not counted, because
        they do not expect a chat reply.
        """
        if not self.settings.proactive_pacing_enabled:
            return False
        state = self.onboarding_pacing_store.get_state(user_id, agent_id)
        if state is None:
            return False
        streak = int(state["unreplied_streak"])
        if streak >= max(1, self.settings.proactive_onboarding_pacing_max_unreplied):
            return True
        if streak > 0:
            last_sent_at = self._parse_timestamp(state.get("last_sent_at"))
            if last_sent_at is not None:
                silent_seconds = self.settings.proactive_onboarding_pacing_silent_seconds
                return (_utcnow() - last_sent_at).total_seconds() >= silent_seconds
        return False

    def _effective_cooldown_seconds(self, state: dict[str, Any]) -> float:
        replied = int(state.get("total_replied") or 0)
        miss_weight = float(state.get("miss_weight") or 0.0)
        opportunities = int(state.get("opportunities") or 0)
        reply_rate = min(1.0, (replied + 1.0) / (opportunities + 2.0))
        lo = float(self.settings.proactive_pacing_min_cooldown_seconds)
        hi = float(self.settings.proactive_pacing_max_cooldown_seconds)
        base = lo + (1.0 - reply_rate) * (hi - lo)
        base = max(lo, min(hi, base))
        effective = base * (
            float(self.settings.proactive_pacing_backoff_growth) ** miss_weight
        )
        return min(effective, float(self.settings.proactive_pacing_max_effective_cooldown_seconds))

    def _effective_max_unreplied(self, state: dict[str, Any]) -> int:
        """Adaptive hard cap: responsive users tolerate more unanswered notes."""
        replied = int(state.get("total_replied") or 0)
        opportunities = int(state.get("opportunities") or 0)
        reply_rate = min(1.0, (replied + 1.0) / (opportunities + 2.0))
        lo = self.settings.proactive_pacing_min_unreplied
        hi = self.settings.proactive_pacing_max_unreplied
        return round(lo + reply_rate * (hi - lo))

    def _daily_interval_seconds(self, user_id: str, agent_id: str) -> float:
        """Adaptive daily check cadence: more replies -> checked more often."""
        state = self.pacing_store.get_state(user_id, agent_id)
        sent = int(state["total_sent"] or 0) if state else 0
        replied = int(state["total_replied"] or 0) if state else 0
        reply_rate = (replied + 1.0) / (sent + 2.0)
        lo = float(self.settings.proactive_daily_tick_min_seconds)
        hi = float(self.settings.proactive_daily_tick_max_seconds)
        interval = lo + (1.0 - reply_rate) * (hi - lo)
        return max(lo, min(hi, interval))

    def _is_daily_due(
        self,
        user_id: str,
        agent_id: str,
        now: datetime | None = None,
    ) -> bool:
        state = self.pacing_store.get_state(user_id, agent_id)
        if state is None or not state.get("next_daily_due_at"):
            return True
        due = self._parse_timestamp(state["next_daily_due_at"])
        if due is None:
            return True
        return (now or _utcnow()) >= due

    def _probably_asleep(self, user_id: str, agent_id: str) -> bool | None:
        """Return True/False when fresh sleep-stage data is decisive, else None."""
        if not self.settings.proactive_sleep_detection_enabled or self.health_store is None:
            return None
        sample = self.health_store.latest_sample(user_id, "SLEEP_STAGE")
        if sample is None or sample.bucket_start is None or sample.value1 is None:
            return None
        bucket = self._parse_timestamp(sample.bucket_start)
        if bucket is None:
            return None
        age = (_utcnow() - bucket).total_seconds()
        if age < 0 or age > self.settings.proactive_sleep_stage_freshness_seconds:
            return None
        stage = int(sample.value1)
        if stage in (1, 2, 3):
            return True
        if stage == 4:
            return False
        return None

    def _is_do_not_disturb(
        self,
        user_id: str,
        agent_id: str,
        *,
        fallback_quiet_hours: bool = True,
    ) -> bool:
        """Suppress delivery when the user is likely asleep, otherwise fall back
        to the configured fixed quiet window when no fresh signal is available.

        ``fallback_quiet_hours`` lets the onboarding path skip the fixed quiet
        window: a brand-new user usually has no sleep-stage data yet, so the
        fixed window would otherwise block their first welcome message entirely
        during, for example, a late-night install. Onboarding still honors real
        sleep-stage evidence via ``_probably_asleep``.
        """
        asleep = self._probably_asleep(user_id, agent_id)
        if asleep is True:
            return True
        if asleep is False:
            return False
        if not fallback_quiet_hours:
            return False
        return self._within_quiet_hours()

    def _user_timezone(self, user_id: str) -> str:
        if self.timezone_resolver is not None:
            resolved = self.timezone_resolver(user_id)
            if resolved:
                return resolved
        return (
            self.settings.reminder_timezone
            or self.settings.default_timezone
            or "Asia/Shanghai"
        )

    @staticmethod
    def _clock_text(minutes: float) -> str:
        """Round a minute-of-day value to a human-readable HH:MM clock time."""
        rounded = int(round(minutes / 5.0)) * 5
        if rounded >= 1440:
            rounded -= 1440
        return f"{rounded // 60:02d}:{rounded % 60:02d}"

    def _infer_routine_from_health(self, user_id: str) -> str | None:
        """Infer the user's typical sleep/wake clock times from main sleep sessions."""
        if self.health_store is None:
            return None
        tz_name = self._user_timezone(user_id)
        tz = resolve_zoneinfo(tz_name)
        today = datetime.now(tz).date()
        from_day = (today - timedelta(days=6)).isoformat()
        to_day = today.isoformat()
        try:
            _, samples = self.health_store.get_metrics(
                user_id, from_day, to_day, tz_name
            )
        except Exception:
            return None

        starts: list[float] = []
        ends: list[float] = []
        for sample in samples:
            if getattr(sample, "metric_type", None) != "SLEEP_SESSION":
                continue
            if getattr(sample, "value4", None) != "main":
                continue
            start = self._parse_timestamp(getattr(sample, "bucket_start", None))
            end = self._parse_timestamp(getattr(sample, "bucket_end", None))
            if start is None or end is None:
                continue
            local_start = start.astimezone(tz)
            local_end = end.astimezone(tz)
            starts.append(local_start.hour * 60 + local_start.minute)
            ends.append(local_end.hour * 60 + local_end.minute)

        if len(starts) < 2:
            return None

        bed = self._clock_text(median(starts))
        wake = self._clock_text(median(ends))
        return f"最近 {len(starts)} 晚，你通常约 {bed} 入睡、{wake} 左右醒来。"

    async def _plan_onboarding(
        self,
        user_id: str,
        agent_id: str,
        eligible: list[str],
        guide_open: list[str],
        *,
        first_contact: bool,
    ) -> dict[str, Any]:
        scope = MemoryScope(user_id=user_id, agent_id=agent_id)
        snapshot = await self.memory_service.snapshot(scope)
        memory_prompt = self.memory_service.build_system_prompt(snapshot)
        location = self.presence.location(user_id, agent_id)
        location_text = (
            f"{location[0]}, {location[1]}" if location is not None else "未知"
        )
        completed = self.profile_store.completed_slots(user_id, agent_id)
        filled = {slot for slot in completed if slot in PROFILE_SLOTS}
        delivered = {slot for slot in completed if slot in GUIDE_SLOTS}
        pending = self.profile_store.pending_slots(user_id, agent_id)
        skipped = self.profile_store.skipped_slots(user_id, agent_id)
        deferred = self.profile_store.deferred_slots(user_id, agent_id)
        conversation_text = await self._recent_conversation_text(
            user_id, agent_id
        )
        messages = [
            {"role": "system", "content": PROFILE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Open profile fields: {', '.join(eligible) or '无'}\n"
                    f"Open guide items: {', '.join(guide_open) or '无'}\n"
                    f"Already filled profile fields: "
                    f"{', '.join(sorted(filled)) or '无'}\n"
                    f"Already delivered guide items: "
                    f"{', '.join(sorted(delivered)) or '无'}\n"
                    f"Pending slots (already asked, awaiting reply): "
                    f"{', '.join(sorted(pending)) or '无'}\n"
                    f"Skipped slots: "
                    f"{', '.join(sorted(skipped)) or '无'}\n"
                    f"Deferred slots (slow phase may revisit): "
                    f"{', '.join(sorted(deferred)) or '无'}\n"
                    f"First contact: {'yes' if first_contact else 'no'}\n"
                    f"GPS location: {location_text}\n"
                    f"Xiaomi status: {self._xiaomi_status_text(scope.user_id)}\n"
                    f"Current conversation:\n{conversation_text}\n"
                    f"Known user info:\n{memory_prompt}"
                ),
            },
        ]
        with token_context(kind="proactive_profile", user_id=scope.user_id):
            response = await self.llm.complete(
                messages,
                tools=None,
                max_tokens=self.settings.proactive_decision_max_tokens,
            )
        return self._parse_profile_plan(response.content)

    @staticmethod
    def _parse_profile_plan(content: str) -> dict[str, Any]:
        data = ProactiveEngine._extract_json(content)
        if not isinstance(data, dict):
            data = {}
        slot = data.get("slot")
        message = data.get("message")
        return {
            "slot": slot if isinstance(slot, str) else None,
            "message": message if isinstance(message, str) else None,
        }

    async def _decide(
        self,
        scope: MemoryScope,
        snapshot: Any,
        observations: list[Any],
        candidates: list[ProactiveCategory],
        trending_items: list[Any],
        trigger_source: str = "time",
        situation_snapshot: SituationSnapshot | None = None,
    ) -> dict[str, Any]:
        memory_prompt = self.memory_service.build_system_prompt(snapshot)
        now = datetime.now(resolve_zoneinfo(self._user_timezone(scope.user_id)))
        situation_text = "No pre-built situation snapshot is available."
        if situation_snapshot is not None:
            situation_text = situation_snapshot.context_text()

        event_context = "No structured event memory is available yet."
        observation_lines = []
        conversation_text = "No active conversation."
        if situation_snapshot is None:
            if self.event_memory_service is not None:
                try:
                    event_context = await self.event_memory_service.build_context(
                        scope,
                        now=now,
                    )
                except Exception:
                    pass
            observation_lines = [
                f"- {observation.source.value}/{observation.kind} @ "
                f"{observation.observed_at.isoformat()}: "
                f"{json.dumps(observation.payload, ensure_ascii=False)}"
                for observation in observations
            ]
            conversation_text = await self._recent_conversation_text(
                scope.user_id, scope.agent_id
            )
        else:
            event_context = "Included in the situation snapshot above."
            observation_lines = ["Included in the situation snapshot above."]
            conversation_text = "Included in the situation snapshot above."
        observations_text = "\n".join(observation_lines) or "No recent observations."
        past_keys = [
            entry.insight_key
            for entry in self.told_store.list(scope.user_id, scope.agent_id)
        ]
        boost = TRIGGER_CATEGORY_BOOST.get(trigger_source or "time", {})
        candidate_line_items: list[str] = []
        for category in candidates:
            preference = self.preference_store.score(
                scope.user_id,
                scope.agent_id,
                category.value,
            )
            preference *= boost.get(category, 1.0)
            candidate_line_items.append(
                f"- {category.value}: {CATEGORY_GUIDANCE.get(category, '')} "
                f"(preference hint {preference:.2f})"
            )
        candidate_lines = "\n".join(candidate_line_items)
        trending_lines: list[str] = []
        for item in trending_items[:6]:
            title = str(getattr(item, "title", "") or "")
            snippet = str(getattr(item, "snippet", "") or "")
            url = str(getattr(item, "url", "") or "")
            trending_lines.append(f"- {title}\n  {snippet}\n  {url}")
        trending_text = "\n".join(trending_lines) or "（无）"
        trigger_text = TRIGGER_SOURCE_DESCRIPTION.get(
            trigger_source, trigger_source
        )
        pacing_state = self.pacing_store.get_state(scope.user_id, scope.agent_id) or {}
        direct_outstanding, interactive_outstanding = self.store.outstanding_counts(
            scope.user_id, scope.agent_id
        )
        pacing_guidance = (
            f"mode={pacing_state.get('mode', 'normal')}; "
            f"direct_outstanding={direct_outstanding}; "
            f"interactive_outstanding={interactive_outstanding}. "
            "If one direct question is outstanding, do not send another direct_question. "
            "If two interactive messages are outstanding, choose share or stay silent. "
            "During resting recovery probes, prefer a low-pressure share or soft_check_in."
        )
        messages = [
            {"role": "system", "content": PROACTIVE_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Current local time (authoritative): {now.isoformat()}\n"
                    f"Trigger: {trigger_text}\n\n"
                    f"Relationship pacing: {pacing_guidance}\n\n"
                    f"Situation snapshot (authoritative for evidence ids and "
                    f"freshness):\n{situation_text}\n\n"
                    f"Available categories (choose any, and feel free to explore "
                    f"a new topic):\n{candidate_lines}\n\n"
                    f"Already-told topics (avoid repeating these for explore): "
                    f"{', '.join(past_keys[:40])}\n\n"
                    f"Available trending items (use only if you choose the "
                    f"trending category):\n{trending_text}\n\n"
                    f"Current conversation:\n{conversation_text}\n\n"
                    f"Durable memory:\n{memory_prompt}\n\n"
                    f"Structured event timeline:\n{event_context}\n\n"
                    f"Recent observations:\n{observations_text}"
                ),
            },
        ]
        tools = self.tool_factory(scope) if self.tool_factory else []
        with token_context(kind="proactive", user_id=scope.user_id):
            content, tool_calls = await self._complete_decision(
                messages,
                tools=tools,
                max_tokens=self.settings.proactive_decision_max_tokens,
                max_rounds=self.settings.proactive_tool_rounds,
            )
        decision = self._parse_decision(
            content,
            candidates,
            situation_snapshot=situation_snapshot,
            min_grounding_confidence=(
                self.settings.proactive_context_min_grounding_confidence
            ),
        )
        decision["tool_calls"] = tool_calls
        return decision

    async def _complete_decision(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[Tool],
        max_tokens: int,
        max_rounds: int,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run a short tool-calling loop before the proactive decision JSON.

        The proactive daily decision can inspect the same data tools as normal
        chat (health, memory, time, weather, etc.) and let the model decide
        whether calling them is useful. After any tool rounds the model still
        has to produce the ``should_message`` JSON consumed by ``_parse_decision``.
        """
        if not tools:
            response = await self.llm.complete(messages, None, max_tokens=max_tokens)
            return response.content or "", []

        tool_schemas = [tool.to_openai_tool() for tool in tools]
        tools_by_name = {tool.name: tool for tool in tools}
        conversation = list(messages)
        tool_audit: list[dict[str, Any]] = []

        for _ in range(max_rounds):
            response = await self.llm.complete(
                conversation,
                tool_schemas,
                max_tokens=max_tokens,
            )
            if not response.tool_calls:
                return response.content or "", tool_audit

            conversation.append(
                {
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "type": "function",
                            "function": {
                                "name": tool_call.name,
                                "arguments": json.dumps(
                                    tool_call.arguments, ensure_ascii=False
                                ),
                            },
                        }
                        for tool_call in response.tool_calls
                    ],
                }
            )

            for tool_call in response.tool_calls:
                tool = tools_by_name.get(tool_call.name)
                if tool is None:
                    result = json.dumps(
                        {"error": f"Unknown tool '{tool_call.name}'"},
                        ensure_ascii=False,
                    )
                    status = "unknown_tool"
                else:
                    try:
                        result = await tool.execute(**tool_call.arguments)
                        status = "ok"
                    except Exception as exc:  # noqa: BLE001 - tool boundary
                        result = json.dumps({"error": str(exc)}, ensure_ascii=False)
                        status = "error"
                tool_audit.append(
                    {
                        "name": tool_call.name,
                        "argument_keys": sorted(tool_call.arguments),
                        "status": status,
                    }
                )
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "content": result,
                    }
                )

        response = await self.llm.complete(conversation, None, max_tokens=max_tokens)
        return response.content or "", tool_audit

    @staticmethod
    def _extract_json(content: str) -> Any:
        content = (content or "").strip()
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    return None
        return None

    @staticmethod
    def _coerce_category(
        value: Any,
        candidates: list[ProactiveCategory],
    ) -> ProactiveCategory:
        """Validate an LLM-provided category against the allowed daily list."""
        if isinstance(value, str):
            try:
                category = ProactiveCategory(value)
            except ValueError:
                category = None
            if category in candidates:
                return category
        if ProactiveCategory.explore in candidates:
            return ProactiveCategory.explore
        if candidates:
            return candidates[0]
        return ProactiveCategory.explore

    @staticmethod
    def _coerce_conversation_intent(value: Any) -> ConversationIntent:
        if isinstance(value, str):
            try:
                return ConversationIntent(value)
            except ValueError:
                pass
        return ConversationIntent.share

    @staticmethod
    def _derive_insight_key(category: ProactiveCategory, message: str) -> str:
        digest = hashlib.sha1(message.encode("utf-8")).hexdigest()[:16]
        return f"{category.value}:{digest}"

    @staticmethod
    def _parse_decision(
        content: str,
        candidates: list[ProactiveCategory],
        *,
        situation_snapshot: SituationSnapshot | None = None,
        min_grounding_confidence: float = 0.5,
    ) -> dict[str, Any]:
        data = ProactiveEngine._extract_json(content)
        if not isinstance(data, dict):
            data = {}

        message = str(data.get("message") or "").strip()
        push_message = str(data.get("push_message") or "").strip() or None
        category = ProactiveEngine._coerce_category(
            data.get("category"), candidates
        )
        conversation_intent = ProactiveEngine._coerce_conversation_intent(
            data.get("conversation_intent")
        )
        explicit_should_message = data.get("should_message")
        should_message = (
            explicit_should_message is True
            if "should_message" in data
            else bool(message)
        )

        summary = str(data.get("situation_summary") or "").strip() or None
        decision_reason = str(data.get("decision_reason") or "").strip() or None
        silence_reason = str(data.get("silence_reason") or "").strip() or None
        try:
            confidence = float(data.get("situation_confidence"))
        except (TypeError, ValueError):
            confidence = None
        if confidence is not None:
            confidence = max(0.0, min(1.0, confidence))

        raw_refs = data.get("evidence_refs")
        evidence_refs = (
            list(dict.fromkeys(item for item in raw_refs if isinstance(item, str)))
            if isinstance(raw_refs, list)
            else []
        )
        if situation_snapshot is not None:
            valid_ids = situation_snapshot.evidence_ids()
            evidence_refs = [item for item in evidence_refs if item in valid_ids]
            if (
                situation_snapshot.interruptibility
                is Interruptibility.do_not_interrupt
            ):
                should_message = False
                silence_reason = silence_reason or "当前情境不适合打扰"

            current_claim_terms = (
                "你正在",
                "你可能正在",
                "你现在在",
                "你还在",
                "此刻你",
                "现在你",
                "看起来你",
                "似乎你",
                "刚运动完",
                "刚睡醒",
            )
            has_current_claim = any(term in message for term in current_claim_terms)
            if should_message and has_current_claim:
                activity_sources = {
                    "conversation",
                    "event_memory",
                    "gps",
                    "health",
                    "observation",
                }
                fresh_evidence = [
                    signal
                    for evidence_id in evidence_refs
                    if (signal := situation_snapshot.signal(evidence_id)) is not None
                    and signal.freshness is SignalFreshness.fresh
                    and signal.source in activity_sources
                ]
                if (
                    confidence is None
                    or confidence < min_grounding_confidence
                    or not fresh_evidence
                ):
                    should_message = False
                    silence_reason = "当前状态断言缺少足够新鲜且可信的证据"

        if not message:
            should_message = False
        if not should_message:
            message = ""
            push_message = None

        insight_key = (
            ProactiveEngine._derive_insight_key(category, message)
            if message
            else None
        )
        return {
            "should_message": should_message,
            "insight_key": insight_key,
            "message": message,
            "push_message": push_message,
            "category": category,
            "conversation_intent": conversation_intent,
            "context_snapshot_id": (
                situation_snapshot.id if situation_snapshot is not None else None
            ),
            "situation_summary": summary,
            "situation_confidence": confidence,
            "evidence_refs": evidence_refs,
            "decision_reason": decision_reason,
            "silence_reason": silence_reason,
        }

    def _audit_situation(
        self,
        snapshot: SituationSnapshot | None,
        decision: ProactiveDecision,
    ) -> None:
        if snapshot is None or self.audit_logger is None:
            return
        try:
            self.audit_logger.write(snapshot, decision, decision.tool_calls)
        except Exception:
            # Audit persistence must not change the user-facing decision path.
            return

    def _audit_gate(
        self,
        user_id: str,
        agent_id: str,
        trigger_source: str,
        outcome: str,
        reason: str,
    ) -> None:
        if self.gate_logger is None:
            return
        try:
            self.gate_logger.write(
                user_id=user_id,
                agent_id=agent_id,
                trigger_source=trigger_source,
                outcome=outcome,
                reason=reason,
                pacing_state=self.pacing_store.get_state(user_id, agent_id),
                outstanding=self.store.outstanding_counts(user_id, agent_id),
            )
        except Exception:
            # Audit persistence must not change the user-facing decision path.
            return
