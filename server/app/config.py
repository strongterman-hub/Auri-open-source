from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="AURI_",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Auri Server"
    environment: str = "development"
    api_prefix: str = "/v1"
    debug_ui_enabled: bool = False

    # Admin dashboard HTTP Basic credentials. When either is unset the admin
    # dashboard is disabled (503).
    admin_username: str | None = None
    admin_password: str | None = None

    # Registration email verification. Resend is accessed over HTTPS so the
    # application server does not need direct SMTP/port-25 delivery.
    resend_api_key: str | None = None
    auth_email_from: str = "Auri <no-reply@your-domain.example>"
    auth_email_verification_required: bool = False
    auth_code_secret: str | None = None
    auth_code_ttl_seconds: int = 600
    auth_code_resend_after_seconds: int = 60
    auth_code_max_attempts: int = 5
    auth_code_max_sends_per_email_hour: int = 5
    auth_code_max_sends_per_client_hour: int = 20
    auth_email_timeout_seconds: float = 15.0

    # Credits are sold at 95 per yuan and redeemed against actual provider
    # cost at 100 Credits per yuan. This leaves a 5% gross service margin.
    billing_welcome_credits: int = 500
    billing_credits_per_yuan: int = 95
    billing_min_recharge_yuan: int = 1
    billing_max_recharge_yuan: int = 1000
    billing_enforcement_enabled: bool = False

    # Alipay is opt-in so an incomplete key deployment cannot create unusable
    # orders. Private key material belongs only in the server environment.
    alipay_enabled: bool = False
    alipay_app_id: str | None = None
    alipay_private_key: str | None = None
    alipay_private_key_file: Path | None = None
    alipay_public_key: str | None = None
    alipay_public_key_file: Path | None = None
    alipay_gateway: str = "https://openapi-sandbox.dl.alipaydev.com/gateway.do"
    alipay_notify_url: str = (
        "https://your-domain.example/v1/billing/alipay/notify"
    )
    alipay_timeout_seconds: float = 15.0

    data_dir: Path = Field(default_factory=lambda: Path("data"))
    default_memory_char_limit: int = 2200
    default_user_char_limit: int = 1375
    memory_backend: str = "file"
    # Background consolidation (dreaming-lite). Write approval is reserved and
    # not yet enforced by the memory tool.
    memory_consolidation_max_candidates: int = 10
    memory_consolidation_max_tokens: int = 2000
    memory_write_approval: bool = False
    # Structured conversation-event memory. Raw owner messages are always
    # captured when enabled; semantic extraction is independently switchable.
    event_memory_enabled: bool = True
    event_extraction_enabled: bool = True
    event_extraction_max_tokens: int = 4096
    event_context_limit: int = 12
    event_detail_recall_threshold: float = 0.28
    event_detail_low_half_life_days: float = 14.0
    event_detail_normal_half_life_days: float = 45.0
    event_detail_high_half_life_days: float = 180.0
    event_current_state_ttl_hours: float = 8.0

    cors_origins: list[str] = Field(default_factory=lambda: ["*"])

    llm_provider: str = "openai-compatible"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str | None = None
    llm_model: str = "gpt-4.1-mini"
    # Model used for image-bearing turns. DeepSeek's vision model is opt-in and
    # selected per request only when the conversation contains an image.
    llm_vision_model: str = "deepseek-v4-flash-vision-exp"

    # Context compression. Auri keeps the full transcript on disk and injects a
    # rolling summary plus a recent tail into the prompt once the assembled
    # context approaches the model window.
    context_length_override: int | None = None
    default_context_length: int = 128_000
    context_chars_per_token: int = 4
    compression_threshold_ratio: float = 0.75
    compression_tail_ratio: float = 0.50
    compression_summary_ratio: float = 0.20
    compression_min_summary_tokens: int = 2000
    compression_max_summary_tokens: int = 4000
    compression_summary_model: str | None = None

    # Session reset policy. Time is a hygiene axis, not the primary session
    # boundary; see the handoff planning doc. `none` keeps the long-chat model.
    session_reset_policy: str = "none"  # none | idle | daily | both
    session_idle_minutes: int = 1440
    session_daily_at_hour: int = 4

    # Friend-like asynchronous chat replies. User messages are acknowledged and
    # persisted first; a background worker later replies, delays, or intentionally
    # stays silent for a high-confidence conversational closing.
    chat_reply_enabled: bool = True
    chat_reply_tick_seconds: float = 0.5
    chat_reply_debounce_seconds: float = 3.0
    chat_reply_planner_model: str | None = None
    chat_reply_planner_max_tokens: int = 2048
    chat_reply_fast_min_seconds: float = 1.5
    chat_reply_fast_max_seconds: float = 6.0
    chat_reply_normal_min_seconds: float = 8.0
    chat_reply_normal_max_seconds: float = 30.0
    chat_reply_away_min_seconds: float = 30.0
    chat_reply_away_max_seconds: float = 300.0
    chat_reply_max_attempts: int = 3
    chat_reply_retry_seconds: float = 15.0

    # Proactive messaging. The engine is disabled by default and only runs when
    # enabled, so existing deployments are unchanged until the feature is turned on.
    proactive_enabled: bool = False
    proactive_tick_seconds: int = 300
    # Per-user adaptive cadence for the daily time tick. The scheduler wakes on
    # the short base interval and evaluates each user only when their own due
    # time has passed. Users who reply more get checked more often.
    proactive_daily_tick_min_seconds: int = 300
    proactive_daily_tick_max_seconds: int = 1800
    # Onboarding messages run on a much shorter cadence so a brand-new user gets
    # an immediate, dense welcome flow without affecting the daily proactive loop.
    proactive_onboarding_tick_seconds: int = 60
    proactive_onboarding_slow_tick_seconds: int = 3600
    proactive_onboarding_cooldown_seconds: int = 30
    proactive_onboarding_slow_cooldown_seconds: int = 86400
    proactive_onboarding_deferred_retry_seconds: int = 604800
    # If a profile question has been left unanswered (for example because the
    # reply assessment misjudged the answer), stop waiting after this long and
    # move on to the next field so onboarding can never be permanently stuck.
    proactive_onboarding_pending_timeout_seconds: int = 1800
    # deepseek-v4-flash is a reasoning model whose ``max_tokens`` budget is shared
    # between reasoning and the final answer. These JSON decision calls need a
    # generous margin so the model never returns an empty ``content``.
    proactive_decision_max_tokens: int = 8192
    proactive_reply_assess_max_tokens: int = 2048
    # Maximum number of tool-calling rounds used by the proactive daily decision
    # before it asks for the final should_message JSON. Kept aligned with the
    # normal chat runner so the proactive decision has the same room to inspect
    # data before committing to a category and message.
    proactive_tool_rounds: int = 6
    # Build a source-balanced situation snapshot before each eligible daily
    # proactive decision instead of relying on optional model tool calls.
    proactive_context_enabled: bool = True
    proactive_context_max_signals: int = 48
    proactive_context_weather_timeout_seconds: float = 3.0
    proactive_context_gps_fresh_seconds: int = 900
    proactive_context_gps_stale_seconds: int = 7200
    proactive_context_conversation_fresh_seconds: int = 1800
    proactive_context_conversation_stale_seconds: int = 21600
    proactive_context_weather_fresh_seconds: int = 1800
    proactive_context_weather_stale_seconds: int = 7200
    proactive_context_health_fresh_seconds: int = 1800
    proactive_context_health_stale_seconds: int = 21600
    proactive_context_min_grounding_confidence: float = 0.5
    # A per-user daily budget is intentionally not a gate in the current phase.
    # The value is kept for compatibility and defaults to an effectively
    # unlimited count if a budget is ever re-enabled.
    proactive_daily_budget: int = 200
    proactive_cooldown_seconds: int = 3600
    # Suppress proactive delivery while the user is actively chatting. If the
    # last message in the active session is an unanswered user message, or the
    # user sent a message within this window, the engine skips the current tick.
    proactive_active_conversation_window_seconds: int = 300
    proactive_model: str | None = None
    proactive_presence_ttl_seconds: int = 5
    proactive_quiet_hours: str = "23-08"  # e.g. "23-08" for 23:00 to 08:00
    proactive_quiet_hours_tz: str = "Asia/Shanghai"

    # Onboarding profile loop.
    proactive_onboarding_enabled: bool = True
    proactive_profile_complete_threshold: float = 0.9
    # Onboarding pressure gate. This is intentionally much lower than the daily
    # adaptive pacing: stop asking new cold-start questions after this many
    # unanswered messages, or after this long without a reply.
    proactive_onboarding_pacing_max_unreplied: int = 2
    proactive_onboarding_pacing_silent_seconds: int = 172800

    # Adaptive proactive pacing. Replaces the flat ``proactive_cooldown_seconds``
    # with a per-user unanswered-streak backoff plus a reply-rate-adapted base
    # cooldown. When disabled the engine falls back to the legacy flat cooldown.
    proactive_pacing_enabled: bool = True
    proactive_pacing_min_cooldown_seconds: int = 1800
    proactive_pacing_max_cooldown_seconds: int = 21600
    proactive_pacing_backoff_growth: float = 2.0
    proactive_pacing_min_unreplied: int = 3
    proactive_pacing_max_unreplied: int = 10
    proactive_pacing_max_effective_cooldown_seconds: int = 86400
    # Reply-expected messages settle only after the client has had time to see
    # and respond. Unknown exposure expires without penalizing relationship pace.
    proactive_engagement_settlement_seconds: int = 21600
    proactive_reply_candidate_seconds: int = 86400

    # Sleep-aware do-not-disturb. When fresh sleep-stage data says the user is
    # asleep, suppress proactive delivery; when it says awake, allow delivery;
    # only fall back to the fixed quiet window when no fresh signal exists.
    proactive_sleep_detection_enabled: bool = True
    proactive_sleep_stage_freshness_seconds: int = 900

    # Push notifications via JPush. When either value is unset the push sender
    # degrades to a no-op, so chat-session delivery keeps working without a vendor.
    jpush_app_key: str | None = None
    jpush_master_secret: str | None = None

    # Background health sync for bound Xiaomi accounts.
    health_sync_enabled: bool = False
    health_sync_tick_seconds: int = 1800

    # Versioned dual sleep scoring. Computation is enabled by default; user
    # visibility stays off until the planned shadow evaluation is complete.
    sleep_dual_score_enabled: bool = True
    sleep_dual_score_visible: bool = False

    # Weather source via Open-Meteo. It is opt-in and requires coordinates;
    # leaving the coordinates unset disables the fetcher even if weather_enabled
    # is true. No API key is required by Open-Meteo.
    weather_enabled: bool = False
    weather_tick_seconds: int = 1800
    weather_latitude: float | None = None
    weather_longitude: float | None = None
    weather_timezone: str = "Asia/Shanghai"
    weather_min_temperature_delta: float = 2.0
    weather_min_humidity_delta: float = 10.0

    # User reminders (time-based and health-condition). The scheduler is on by
    # default because reminders are an explicit user request rather than a
    # proactive-messaging experiment.
    reminder_enabled: bool = True
    reminder_tick_seconds: int = 30
    reminder_timezone: str = "Asia/Shanghai"
    # Fallback timezone used for a user until their device reports one.
    default_timezone: str = "Asia/Shanghai"

    # Live web search and page fetch. openwebsearch is the default and talks to
    # a local open-webSearch daemon; duckduckgo/tavily remain available as
    # fallbacks when a key or daemon is not configured.
    web_search_provider: str = "none"  # none | openwebsearch | duckduckgo | tavily
    web_search_base_url: str = "http://127.0.0.1:3000"
    web_search_engine: str = "bing"
    tavily_api_key: str | None = None
    web_search_timeout_seconds: float = 15.0
    web_fetch_max_chars: int = 12000
    web_user_agent: str = "Auri/0.1 (+https://auri.thinktocode.online)"

    # Daily trending-news sharing. Off by default; enable once the search
    # provider above is working so Auri does not silently fail to send news.
    trending_enabled: bool = False
    trending_tick_seconds: int = 21600
    trending_query: str = "今日热点新闻"
    trending_max_results: int = 6
    trending_cache_seconds: int = 3600

    xiaomi_secret_key: str | None = None
    xiaomi_sync_timeout_seconds: int = 180
    xiaomi_credential_cache: Path | None = None


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance for the process."""

    return Settings()
