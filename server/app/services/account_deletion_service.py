from __future__ import annotations

from typing import Any

from app.auth.store import AuthStore
from app.billing.store import BillingStore
from app.chat.reply_store import ChatReplyStore
from app.files.store import FileStore
from app.health.store import HealthStore
from app.health.sleep_score_store import SleepScoreStore
from app.integrations.xiaomi.credential_store import CredentialStore
from app.memory.intents import IntentStore
from app.memory.events import EventMemoryStore
from app.memory.models import MemoryScope
from app.memory.told import ToldStore
from app.observation.store import ObservationStore
from app.proactive.pacing import ProactivePacingStore
from app.proactive.preferences import PreferenceStore
from app.proactive.profile import ProfileStore
from app.proactive.settings import ProactiveSettingsStore
from app.proactive.store import ProactiveStore
from app.reminders.store import ReminderStore
from app.services.device_store import DeviceStore
from app.services.memory_service import MemoryService
from app.services.presence_service import PresenceService
from app.services.session_service import SessionService
from app.services.timezone_store import UserTimezoneStore
from app.todos import TodoStore


class AccountDeletionService:
    """Deletes every trace of one Auri account and all user-owned data."""

    def __init__(
        self,
        *,
        auth_store: AuthStore,
        billing_store: BillingStore,
        session_service: SessionService,
        memory_service: MemoryService,
        file_store: FileStore,
        health_store: HealthStore,
        sleep_score_store: SleepScoreStore,
        observation_store: ObservationStore,
        told_store: ToldStore,
        intent_store: IntentStore,
        todo_store: TodoStore,
        reminder_store: ReminderStore,
        proactive_store: ProactiveStore,
        proactive_settings_store: ProactiveSettingsStore,
        preference_store: PreferenceStore,
        profile_store: ProfileStore,
        pacing_store: ProactivePacingStore,
        onboarding_pacing_store: ProactivePacingStore,
        device_store: DeviceStore,
        credential_store: CredentialStore,
        presence_service: PresenceService,
        timezone_store: UserTimezoneStore,
        event_store: EventMemoryStore | None = None,
        chat_reply_store: ChatReplyStore | None = None,
        schedule_service=None,
    ) -> None:
        self.auth_store = auth_store
        self.billing_store = billing_store
        self.session_service = session_service
        self.memory_service = memory_service
        self.file_store = file_store
        self.health_store = health_store
        self.sleep_score_store = sleep_score_store
        self.observation_store = observation_store
        self.told_store = told_store
        self.intent_store = intent_store
        self.todo_store = todo_store
        self.reminder_store = reminder_store
        self.proactive_store = proactive_store
        self.proactive_settings_store = proactive_settings_store
        self.preference_store = preference_store
        self.profile_store = profile_store
        self.pacing_store = pacing_store
        self.onboarding_pacing_store = onboarding_pacing_store
        self.device_store = device_store
        self.credential_store = credential_store
        self.presence_service = presence_service
        self.timezone_store = timezone_store
        self.event_store = event_store
        self.chat_reply_store = chat_reply_store
        self.schedule_service = schedule_service

    async def delete_user(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> None:
        sessions = await self.session_service.list_by_user(user_id)
        file_ids: set[str] = set()
        for session in sessions:
            file_ids.update(self._file_ids_from_session(session))

        for file_id in file_ids:
            self.file_store.delete(file_id)

        await self.session_service.delete_by_user(user_id)
        await self.memory_service.delete(
            MemoryScope(user_id=user_id, agent_id=agent_id)
        )

        self.health_store.clear(user_id)
        self.sleep_score_store.clear(user_id)
        self.observation_store.delete_user(user_id, agent_id)
        if self.event_store is not None:
            self.event_store.delete_user(user_id, agent_id)
        self.told_store.delete_user(user_id, agent_id)
        self.intent_store.delete_user(user_id, agent_id)
        self.todo_store.delete_user(user_id, agent_id)
        self.reminder_store.delete_user(user_id, agent_id)
        if self.schedule_service is not None:
            self.schedule_service.delete_user(user_id, agent_id)
        self.proactive_store.delete_user(user_id, agent_id)
        self.proactive_settings_store.delete_user(user_id, agent_id)
        self.preference_store.delete_user(user_id, agent_id)
        self.profile_store.delete_user(user_id, agent_id)
        self.pacing_store.delete_user(user_id, agent_id)
        self.onboarding_pacing_store.delete_user(user_id, agent_id)
        self.device_store.delete_user(user_id, agent_id)
        self.credential_store.delete(user_id)
        self.presence_service.delete_user(user_id, agent_id)
        self.timezone_store.delete_user(user_id)
        if self.chat_reply_store is not None:
            self.chat_reply_store.delete_user(user_id, agent_id)
        self.billing_store.delete_user(user_id)

        self.auth_store.delete_tokens_for_user(user_id)
        self.auth_store.delete_user(user_id)

    @staticmethod
    def _file_ids_from_session(session: Any) -> set[str]:
        file_ids: set[str] = set()
        for message in getattr(session, "messages", []) or []:
            content = message.get("content") if isinstance(message, dict) else None
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "file":
                    continue
                file_info = part.get("file") or {}
                file_id = file_info.get("id") if isinstance(file_info, dict) else None
                if isinstance(file_id, str) and file_id:
                    file_ids.add(file_id)
        return file_ids
