from __future__ import annotations

import secrets
from dataclasses import dataclass

from app.agent.context_compressor import ContextCompressor
from app.agent.llm import LLMClient
from app.agent.provider import build_llm_client
from app.agent.runner import AgentRunner, BasicAgentRunner
from app.agent.tools import (
    CalculatorTool,
    DateAddTool,
    FetchUrlTool,
    HealthDataTool,
    HealthSyncTool,
    HealthStatsTool,
    LocationTool,
    MemorySearchTool,
    MemoryTool,
    NowTool,
    ReminderTool,
    TodoTool,
    Tool,
    UnitConvertTool,
    WeatherTool,
    WebSearchTool,
)
from app.auth.email_sender import NullEmailSender, ResendEmailSender
from app.auth.service import AuthService
from app.auth.store import AuthStore
from app.auth.verification import EmailVerificationService, VerificationCodeStore
from app.billing.alipay import AlipayClient, read_key
from app.billing.service import BillingService
from app.billing.store import BillingStore
from app.chat.reply_planner import ChatReplyPlanner
from app.chat.reply_service import ChatReplyService
from app.chat.reply_store import ChatReplyStore
from app.config import Settings
from app.core.token_logger import TokenLogger
from app.core.turn_logger import TurnLogger
from app.files.store import FileStore
from app.health.store import HealthStore
from app.health.sleep_score_service import SleepScoreService
from app.health.sleep_score_store import SleepScoreStore
from app.integrations.xiaomi.credential_store import CredentialStore, resolve_secret_key
from app.integrations.xiaomi.qr_login import QrLoginManager
from app.integrations.xiaomi.service import XiaomiService
from app.memory.base import MemoryStore
from app.memory.consolidation import Consolidator
from app.memory.event_extraction import EventExtractor
from app.memory.events import EventMemoryStore
from app.memory.intents import IntentStore
from app.memory.models import MemoryScope, OriginClass
from app.memory.provider import default_memory_store_registry
from app.memory.told import ToldStore
from app.observation.models import ObservationSource
from app.observation.registry import (
    NullObservationProvider,
    ObservationSourceRegistry,
)
from app.observation.store import ObservationStore
from app.proactive.delivery import ProactiveDelivery
from app.proactive.context import (
    ProactiveAuditLogger,
    ProactiveContextBuilder,
    ProactiveGateLogger,
)
from app.proactive.engine import ProactiveEngine
from app.proactive.jpush_sender import JpushSender
from app.proactive.pacing import ProactivePacingStore
from app.proactive.preferences import PreferenceStore
from app.proactive.profile import ProfileStore
from app.proactive.push import NullPushSender
from app.proactive.settings import ProactiveSettingsStore
from app.proactive.store import ProactiveStore
from app.reminders.scheduler import ReminderScheduler
from app.reminders.service import ReminderService
from app.reminders.store import ReminderStore
from app.services.agent_service import AgentService
from app.services.account_deletion_service import AccountDeletionService
from app.services.device_store import DeviceStore
from app.services.event_memory_service import EventMemoryService
from app.services.health_service import HealthService
from app.services.memory_service import MemoryService
from app.services.observation_service import ObservationService
from app.services.presence_service import PresenceService
from app.services.session_service import SessionService
from app.services.timezone_store import TimezoneResolver, UserTimezoneStore
from app.services.trending_service import TrendingService
from app.services.weather_service import WeatherService
from app.session.store import FileSessionStore, SessionStore
from app.todos import TodoStore
from app.web.client import WebSearchClient, build_web_search_client


@dataclass
class Container:
    settings: Settings
    memory_store: MemoryStore
    session_store: SessionStore
    auth_store: AuthStore
    auth_service: AuthService
    billing_store: BillingStore
    billing_service: BillingService
    health_store: HealthStore
    sleep_score_store: SleepScoreStore
    sleep_score_service: SleepScoreService
    health_service: HealthService
    observation_store: ObservationStore
    observation_service: ObservationService
    event_store: EventMemoryStore
    event_memory_service: EventMemoryService
    observation_registry: ObservationSourceRegistry
    intent_store: IntentStore
    told_store: ToldStore
    xiaomi_credential_store: CredentialStore
    xiaomi_qr_login: QrLoginManager
    xiaomi_service: XiaomiService
    llm: LLMClient
    token_logger: TokenLogger
    turn_logger: TurnLogger
    memory_service: MemoryService
    session_service: SessionService
    file_store: FileStore
    agent_runner: AgentRunner
    agent_service: AgentService
    chat_reply_store: ChatReplyStore
    chat_reply_planner: ChatReplyPlanner
    chat_reply_service: ChatReplyService
    account_deletion_service: AccountDeletionService
    consolidator: Consolidator
    presence_service: PresenceService
    proactive_store: ProactiveStore
    proactive_settings_store: ProactiveSettingsStore
    preference_store: PreferenceStore
    profile_store: ProfileStore
    pacing_store: ProactivePacingStore
    onboarding_pacing_store: ProactivePacingStore
    proactive_engine: ProactiveEngine
    proactive_context_builder: ProactiveContextBuilder
    proactive_audit_logger: ProactiveAuditLogger
    reminder_store: ReminderStore
    reminder_service: ReminderService
    reminder_scheduler: ReminderScheduler
    web_search_client: WebSearchClient
    trending_service: TrendingService
    weather_service: WeatherService
    todo_store: TodoStore
    timezone_resolver: TimezoneResolver


def create_container(settings: Settings) -> Container:
    settings.data_dir.mkdir(parents=True, exist_ok=True)

    if settings.auth_email_verification_required:
        if not settings.resend_api_key:
            raise RuntimeError(
                "AURI_RESEND_API_KEY is required when email verification is enabled"
            )
        if not settings.auth_code_secret:
            raise RuntimeError(
                "AURI_AUTH_CODE_SECRET is required when email verification is enabled"
            )

    memory_store = default_memory_store_registry.create(settings)
    session_store = FileSessionStore(settings.data_dir / "sessions")
    file_store = FileStore(settings.data_dir / "files")
    auth_store = AuthStore(settings.data_dir / "auth")
    verification_store = VerificationCodeStore(
        settings.data_dir / "auth" / "verification_codes.db",
        secret=settings.auth_code_secret or secrets.token_urlsafe(32),
        ttl_seconds=settings.auth_code_ttl_seconds,
        resend_after_seconds=settings.auth_code_resend_after_seconds,
        max_attempts=settings.auth_code_max_attempts,
        max_sends_per_email_hour=settings.auth_code_max_sends_per_email_hour,
        max_sends_per_client_hour=settings.auth_code_max_sends_per_client_hour,
    )
    email_sender = (
        ResendEmailSender(
            api_key=settings.resend_api_key,
            from_address=settings.auth_email_from,
            timeout_seconds=settings.auth_email_timeout_seconds,
        )
        if settings.resend_api_key
        else NullEmailSender()
    )
    email_verification = EmailVerificationService(verification_store, email_sender)
    auth_service = AuthService(
        auth_store,
        email_verification=email_verification,
        email_verification_required=settings.auth_email_verification_required,
    )
    health_store = HealthStore(settings.data_dir / "health")
    sleep_score_store = SleepScoreStore(health_store.db_path)
    sleep_score_service = SleepScoreService(health_store, sleep_score_store)
    health_service = HealthService(
        health_store,
        sleep_score_service if settings.sleep_dual_score_enabled else None,
    )

    memory_db_path = settings.data_dir / "memory" / "memory.db"
    observation_store = ObservationStore(memory_db_path)
    observation_service = ObservationService(observation_store)
    event_store = EventMemoryStore(memory_db_path)
    observation_registry = ObservationSourceRegistry()
    observation_registry.register(NullObservationProvider(ObservationSource.schedule))
    observation_registry.register(NullObservationProvider(ObservationSource.phone_state))
    intent_store = IntentStore(memory_db_path)
    told_store = ToldStore(memory_db_path)
    device_store = DeviceStore(settings.data_dir / "devices" / "devices.db")
    presence_service = PresenceService(device_store=device_store)
    todo_store = TodoStore(settings.data_dir / "todos" / "todos.db")
    timezone_store = UserTimezoneStore(settings.data_dir / "auth" / "user_timezones.json")
    timezone_resolver = TimezoneResolver(
        timezone_store,
        default_timezone=settings.default_timezone,
    )
    resolve_user_tz = lambda user_id: timezone_resolver.get(user_id)
    weather_service = WeatherService(
        observation_service,
        latitude=settings.weather_latitude,
        longitude=settings.weather_longitude,
        timezone=settings.weather_timezone,
        min_temperature_delta=settings.weather_min_temperature_delta,
        min_humidity_delta=settings.weather_min_humidity_delta,
        location_provider=presence_service,
    )
    proactive_store = ProactiveStore(
        settings.data_dir / "proactive" / "decisions.db"
    )
    proactive_settings_store = ProactiveSettingsStore(
        settings.data_dir / "proactive" / "proactive.db"
    )
    preference_store = PreferenceStore(
        settings.data_dir / "proactive" / "preferences.db"
    )
    profile_store = ProfileStore(
        settings.data_dir / "proactive" / "profile.db"
    )
    pacing_store = ProactivePacingStore(
        settings.data_dir / "proactive" / "pacing.db"
    )
    onboarding_pacing_store = ProactivePacingStore(
        settings.data_dir / "proactive" / "onboarding_pacing.db"
    )
    reminder_store = ReminderStore(
        settings.data_dir / "reminders" / "reminders.db"
    )
    reminder_service = ReminderService(
        reminder_store,
        timezone=settings.reminder_timezone,
        timezone_resolver=resolve_user_tz,
    )
    web_search_client = build_web_search_client(settings)
    trending_service = TrendingService(
        web_search_client,
        settings.trending_query,
        max_results=settings.trending_max_results,
        cache_seconds=settings.trending_cache_seconds,
    )

    xiaomi_secret_key = resolve_secret_key(settings.xiaomi_secret_key, settings.data_dir)
    xiaomi_credential_store = CredentialStore(
        settings.xiaomi_credential_cache or settings.data_dir / "xiaomi_credentials",
        xiaomi_secret_key,
    )
    xiaomi_qr_login = QrLoginManager(xiaomi_credential_store)
    xiaomi_service = XiaomiService(
        health_store,
        xiaomi_credential_store,
        sync_timeout_seconds=settings.xiaomi_sync_timeout_seconds,
        observation_service=observation_service,
        timezone_resolver=timezone_resolver,
        sleep_score_service=sleep_score_service
        if settings.sleep_dual_score_enabled
        else None,
    )

    billing_store = BillingStore(
        settings.data_dir / "billing" / "billing.db",
        welcome_credits=settings.billing_welcome_credits,
    )
    alipay = None
    if settings.alipay_enabled:
        private_key = read_key(
            settings.alipay_private_key, settings.alipay_private_key_file
        )
        public_key = read_key(
            settings.alipay_public_key, settings.alipay_public_key_file
        )
        if not settings.alipay_app_id or not private_key or not public_key:
            raise RuntimeError(
                "Alipay requires app id, application private key and Alipay public key"
            )
        alipay = AlipayClient(
            app_id=settings.alipay_app_id,
            private_key=private_key,
            alipay_public_key=public_key,
            gateway=settings.alipay_gateway,
            notify_url=settings.alipay_notify_url,
            timeout_seconds=settings.alipay_timeout_seconds,
        )
    billing_service = BillingService(
        billing_store,
        alipay=alipay,
        credits_per_yuan=settings.billing_credits_per_yuan,
        min_recharge_yuan=settings.billing_min_recharge_yuan,
        max_recharge_yuan=settings.billing_max_recharge_yuan,
        enforcement_enabled=settings.billing_enforcement_enabled,
    )
    token_logger = TokenLogger(
        settings.data_dir / "logs" / "token_usage.jsonl",
        usage_sink=billing_service.charge_usage,
    )
    turn_logger = TurnLogger(settings.data_dir / "logs" / "agent_turns.jsonl")
    llm = build_llm_client(
        settings,
        token_logger=token_logger,
        usage_guard=billing_service.ensure_current_user_available,
    )
    compressor = ContextCompressor(llm, settings)

    memory_service = MemoryService(memory_store)
    event_extractor = (
        EventExtractor(llm, max_tokens=settings.event_extraction_max_tokens)
        if settings.event_memory_enabled and settings.event_extraction_enabled
        else None
    )
    event_memory_service = EventMemoryService(
        event_store,
        observation_store,
        extractor=event_extractor,
        timezone_resolver=resolve_user_tz,
        context_limit=settings.event_context_limit,
        detail_threshold=settings.event_detail_recall_threshold,
        low_half_life_days=settings.event_detail_low_half_life_days,
        normal_half_life_days=settings.event_detail_normal_half_life_days,
        high_half_life_days=settings.event_detail_high_half_life_days,
        current_state_ttl_hours=settings.event_current_state_ttl_hours,
    )
    session_service = SessionService(session_store)
    chat_reply_store = ChatReplyStore(settings.data_dir / "chat" / "replies.db")
    chat_reply_planner = ChatReplyPlanner(
        llm,
        model=settings.chat_reply_planner_model,
        max_tokens=settings.chat_reply_planner_max_tokens,
    )
    proactive_context_builder = ProactiveContextBuilder(
        session_service=session_service,
        observation_service=observation_service,
        presence=presence_service,
        reminder_store=reminder_store,
        todo_store=todo_store,
        pacing_store=pacing_store,
        health_store=health_store,
        weather_service=weather_service if settings.weather_enabled else None,
        event_memory_service=(
            event_memory_service if settings.event_memory_enabled else None
        ),
        timezone_resolver=resolve_user_tz,
        presence_ttl_seconds=settings.proactive_presence_ttl_seconds,
        gps_fresh_seconds=settings.proactive_context_gps_fresh_seconds,
        gps_stale_seconds=settings.proactive_context_gps_stale_seconds,
        conversation_fresh_seconds=(
            settings.proactive_context_conversation_fresh_seconds
        ),
        conversation_stale_seconds=(
            settings.proactive_context_conversation_stale_seconds
        ),
        weather_fresh_seconds=settings.proactive_context_weather_fresh_seconds,
        weather_stale_seconds=settings.proactive_context_weather_stale_seconds,
        health_fresh_seconds=settings.proactive_context_health_fresh_seconds,
        health_stale_seconds=settings.proactive_context_health_stale_seconds,
        weather_timeout_seconds=(
            settings.proactive_context_weather_timeout_seconds
        ),
        max_signals=settings.proactive_context_max_signals,
    )
    proactive_audit_logger = ProactiveAuditLogger(
        settings.data_dir / "logs" / "proactive_context.jsonl"
    )
    proactive_gate_logger = ProactiveGateLogger(
        settings.data_dir / "logs" / "proactive_gate.jsonl"
    )
    account_deletion_service = AccountDeletionService(
        auth_store=auth_store,
        billing_store=billing_store,
        session_service=session_service,
        memory_service=memory_service,
        file_store=file_store,
        health_store=health_store,
        sleep_score_store=sleep_score_store,
        observation_store=observation_store,
        told_store=told_store,
        intent_store=intent_store,
        todo_store=todo_store,
        reminder_store=reminder_store,
        proactive_store=proactive_store,
        proactive_settings_store=proactive_settings_store,
        preference_store=preference_store,
        profile_store=profile_store,
        pacing_store=pacing_store,
        onboarding_pacing_store=onboarding_pacing_store,
        device_store=device_store,
        credential_store=xiaomi_credential_store,
        presence_service=presence_service,
        timezone_store=timezone_store,
        event_store=event_store,
        chat_reply_store=chat_reply_store,
    )
    agent_runner = BasicAgentRunner(
        llm,
        turn_logger=turn_logger,
        vision_model=settings.llm_vision_model,
    )

    consolidator = Consolidator(
        llm=llm,
        memory_service=memory_service,
        observation_store=observation_store,
        log_path=settings.data_dir / "memory" / "consolidation_log.jsonl",
        max_candidates=settings.memory_consolidation_max_candidates,
        max_tokens=settings.memory_consolidation_max_tokens,
    )

    def tool_factory(scope: MemoryScope) -> list[Tool]:
        return [
            MemoryTool(memory_service=memory_service, scope=scope, origin=OriginClass.agent),
            MemorySearchTool(
                observation_service=observation_service,
                memory_service=memory_service,
                event_memory_service=(
                    event_memory_service if settings.event_memory_enabled else None
                ),
                scope=scope,
            ),
            HealthDataTool(
                health_service=health_service,
                user_id=scope.user_id,
                timezone_resolver=resolve_user_tz,
            ),
            HealthSyncTool(
                xiaomi_service=xiaomi_service,
                user_id=scope.user_id,
            ),
            HealthStatsTool(
                health_service=health_service,
                user_id=scope.user_id,
                timezone_name=settings.reminder_timezone,
                timezone_resolver=resolve_user_tz,
            ),
            NowTool(
                timezone_name=settings.reminder_timezone,
                timezone_resolver=resolve_user_tz,
                user_id=scope.user_id,
            ),
            ReminderTool(
                service=reminder_service,
                user_id=scope.user_id,
                agent_id=scope.agent_id,
                timezone_resolver=resolve_user_tz,
            ),
            WebSearchTool(client=web_search_client),
            FetchUrlTool(
                max_chars=settings.web_fetch_max_chars,
                timeout=settings.web_search_timeout_seconds,
                user_agent=settings.web_user_agent,
            ),
            CalculatorTool(),
            UnitConvertTool(),
            DateAddTool(),
            TodoTool(
                store=todo_store,
                user_id=scope.user_id,
                agent_id=scope.agent_id,
            ),
            WeatherTool(
                weather_service=weather_service,
                presence=presence_service,
                user_id=scope.user_id,
                agent_id=scope.agent_id,
                fallback_latitude=settings.weather_latitude,
                fallback_longitude=settings.weather_longitude,
                default_forecast_days=3,
                fresh_location_seconds=(
                    settings.proactive_context_gps_fresh_seconds
                ),
                max_location_age_seconds=(
                    settings.proactive_context_gps_stale_seconds
                ),
            ),
            LocationTool(
                presence=presence_service,
                weather_service=weather_service,
                user_id=scope.user_id,
                agent_id=scope.agent_id,
                max_age_seconds=600,
            ),
        ]

    proactive_read_only_tool_names = {
        "memory_search",
        "read_health_data",
        "health_stats",
        "get_current_time",
        "get_current_location",
        "weather",
    }

    def proactive_tool_factory(scope: MemoryScope) -> list[Tool]:
        return [
            tool
            for tool in tool_factory(scope)
            if tool.name in proactive_read_only_tool_names
        ]

    agent_service = AgentService(
        session_service=session_service,
        memory_service=memory_service,
        runner=agent_runner,
        tool_factory=tool_factory,
        compressor=compressor,
        settings=settings,
        file_store=file_store,
        xiaomi_status_provider=xiaomi_service.status,
        timezone_resolver=resolve_user_tz,
        event_memory_service=(
            event_memory_service if settings.event_memory_enabled else None
        ),
    )

    if settings.jpush_app_key and settings.jpush_master_secret:
        push_sender = JpushSender(
            settings.jpush_app_key,
            settings.jpush_master_secret,
        )
    else:
        push_sender = NullPushSender()

    proactive_delivery = ProactiveDelivery(
        session_service,
        presence_service,
        push_sender,
        presence_ttl_seconds=settings.proactive_presence_ttl_seconds,
    )
    proactive_engine = ProactiveEngine(
        llm=llm,
        memory_service=memory_service,
        observation_service=observation_service,
        session_service=session_service,
        told_store=told_store,
        intent_store=intent_store,
        store=proactive_store,
        delivery=proactive_delivery,
        settings=settings,
        presence=presence_service,
        settings_store=proactive_settings_store,
        preference_store=preference_store,
        profile_store=profile_store,
        pacing_store=pacing_store,
        onboarding_pacing_store=onboarding_pacing_store,
        xiaomi_service=xiaomi_service,
        health_store=health_store,
        trending_service=trending_service,
        tool_factory=proactive_tool_factory,
        timezone_resolver=resolve_user_tz,
        event_memory_service=(
            event_memory_service if settings.event_memory_enabled else None
        ),
        context_builder=(
            proactive_context_builder
            if settings.proactive_context_enabled
            else None
        ),
        audit_logger=proactive_audit_logger,
        gate_logger=proactive_gate_logger,
        has_pending_chat=chat_reply_store.has_open,
    )

    chat_reply_service = ChatReplyService(
        store=chat_reply_store,
        planner=chat_reply_planner,
        agent_service=agent_service,
        session_service=session_service,
        presence=presence_service,
        push_sender=push_sender,
        debounce_seconds=settings.chat_reply_debounce_seconds,
        fast_delay=(
            settings.chat_reply_fast_min_seconds,
            settings.chat_reply_fast_max_seconds,
        ),
        normal_delay=(
            settings.chat_reply_normal_min_seconds,
            settings.chat_reply_normal_max_seconds,
        ),
        away_delay=(
            settings.chat_reply_away_min_seconds,
            settings.chat_reply_away_max_seconds,
        ),
        max_attempts=settings.chat_reply_max_attempts,
        retry_seconds=settings.chat_reply_retry_seconds,
        audit_path=settings.data_dir / "logs" / "chat_reply.jsonl",
    )

    reminder_scheduler = ReminderScheduler(
        reminder_store=reminder_store,
        session_service=session_service,
        health_store=health_store,
        delivery=proactive_delivery,
        timezone_name=settings.reminder_timezone,
        timezone_resolver=resolve_user_tz,
        tick_seconds=settings.reminder_tick_seconds,
    )

    agent_service.proactive_reply_hook = proactive_engine.record_reply
    agent_service.proactive_activity_hook = proactive_engine.record_user_activity
    agent_service.is_onboarding = proactive_engine.is_dense_onboarding
    agent_service.next_onboarding_guide = proactive_engine.next_onboarding_guide
    agent_service.mark_onboarding_guide_delivered = (
        proactive_engine.mark_onboarding_guide_delivered
    )
    agent_service.dense_onboarding_hook = proactive_engine.dense_step_after_reply
    agent_service.onboarding_context_provider = (
        proactive_engine.dense_onboarding_context
    )

    return Container(
        settings=settings,
        memory_store=memory_store,
        session_store=session_store,
        auth_store=auth_store,
        auth_service=auth_service,
        billing_store=billing_store,
        billing_service=billing_service,
        health_store=health_store,
        sleep_score_store=sleep_score_store,
        sleep_score_service=sleep_score_service,
        health_service=health_service,
        observation_store=observation_store,
        observation_service=observation_service,
        event_store=event_store,
        event_memory_service=event_memory_service,
        observation_registry=observation_registry,
        intent_store=intent_store,
        told_store=told_store,
        xiaomi_credential_store=xiaomi_credential_store,
        xiaomi_qr_login=xiaomi_qr_login,
        xiaomi_service=xiaomi_service,
        llm=llm,
        token_logger=token_logger,
        turn_logger=turn_logger,
        memory_service=memory_service,
        session_service=session_service,
        file_store=file_store,
        agent_runner=agent_runner,
        agent_service=agent_service,
        chat_reply_store=chat_reply_store,
        chat_reply_planner=chat_reply_planner,
        chat_reply_service=chat_reply_service,
        account_deletion_service=account_deletion_service,
        consolidator=consolidator,
        presence_service=presence_service,
        proactive_store=proactive_store,
        proactive_settings_store=proactive_settings_store,
        preference_store=preference_store,
        profile_store=profile_store,
        pacing_store=pacing_store,
        onboarding_pacing_store=onboarding_pacing_store,
        proactive_engine=proactive_engine,
        proactive_context_builder=proactive_context_builder,
        proactive_audit_logger=proactive_audit_logger,
        reminder_store=reminder_store,
        reminder_service=reminder_service,
        reminder_scheduler=reminder_scheduler,
        web_search_client=web_search_client,
        trending_service=trending_service,
        weather_service=weather_service,
        todo_store=todo_store,
        timezone_resolver=timezone_resolver,
    )
