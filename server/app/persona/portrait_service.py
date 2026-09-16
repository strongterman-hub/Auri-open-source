from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.config import Settings
from app.memory.models import MemoryScope
from app.persona.models import PortraitSettings, PortraitState
from app.persona.portrait import (
    DEFAULT_VARIANT,
    PRESENTATIONS,
    image_url,
    image_url_small,
    pick_variant,
    resolve_avatar,
    resolve_images,
    resolve_mood,
    resolve_time_slot,
)
from app.persona.store import PersonaStore


_LOW_MOOD_MARKERS = (
    "难过",
    "难受",
    "烦",
    "崩溃",
    "焦虑",
    "压力",
    "委屈",
    "生气",
    "累",
    "恶心",
    "不开心",
    "低落",
    "撑不住",
)
_CHEERFUL_MOOD_MARKERS = (
    "开心",
    "高兴",
    "太好了",
    "好棒",
    "顺利",
    "搞定了",
    "通过了",
    "升职",
    "赢了",
    "哈哈",
    "嘿嘿",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return _aware(datetime.fromisoformat(str(value)))
    except ValueError:
        return None


def _parse_csv(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in str(value).split(",") if item.strip()}


def _local_zone(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(str(timezone_name or "Asia/Shanghai"))
    except Exception:
        return ZoneInfo("Asia/Shanghai")


def _classify_workout(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if any(marker in value for marker in ("walk", "hike", "步行", "健走")):
        return "步行"
    if any(
        marker in value
        for marker in ("rid", "cycl", "bike", "骑行", "单车")
    ):
        return "骑行"
    if any(marker in value for marker in ("run", "jog", "跑步")):
        return "跑步"
    return "运动"


def mood_from_text(text: str) -> str | None:
    """Deterministic low-cost mood hint used instead of an extra LLM call."""

    raw = text or ""
    if any(marker in raw for marker in _LOW_MOOD_MARKERS):
        return "low"
    if any(marker in raw for marker in _CHEERFUL_MOOD_MARKERS):
        return "cheerful"
    return None


class PortraitService:
    """Resolve, persist and expose the smart chat-background state."""

    def __init__(
        self,
        *,
        store: PersonaStore,
        settings: Settings,
        sleep_score_store: Any = None,
        health_store: Any = None,
        timezone_resolver: Any = None,
        relationship_provider: Callable[[MemoryScope], Any] | None = None,
        presentation_provider: Callable[[MemoryScope], str | None] | None = None,
        static_root: Path | str | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.sleep_score_store = sleep_score_store
        self.health_store = health_store
        self.timezone_resolver = timezone_resolver
        self.relationship_provider = relationship_provider
        self.presentation_provider = presentation_provider
        self.static_root = static_root
        self._canary = _parse_csv(settings.portrait_canary_user_ids)
        default_presentation = str(settings.portrait_default_presentation or "female")
        self.default_presentation = (
            default_presentation
            if default_presentation in PRESENTATIONS
            else PRESENTATIONS[0]
        )

    # -- availability / preferences -------------------------------------

    def feature_available(self, scope: MemoryScope) -> bool:
        if not self.settings.portrait_enabled:
            return False
        if self._canary and scope.user_id not in self._canary:
            return False
        return True

    def can_use_characters(self, scope: MemoryScope) -> bool:
        """Character prompt layer follows the portrait rollout, not the UI switch."""

        return self.feature_available(scope)

    def get_settings(self, scope: MemoryScope) -> PortraitSettings:
        return self.store.get_portrait_settings(scope.user_id, scope.agent_id)

    def update_settings(
        self,
        scope: MemoryScope,
        *,
        smart_background_enabled: bool,
    ) -> PortraitSettings:
        settings = PortraitSettings(
            smart_background_enabled=bool(smart_background_enabled),
            updated_at=_utcnow(),
        )
        self.store.set_portrait_settings(scope.user_id, scope.agent_id, settings)
        return settings

    def is_enabled(self, scope: MemoryScope) -> bool:
        if not self.feature_available(scope):
            return False
        return self.get_settings(scope).smart_background_enabled

    # -- resolution ------------------------------------------------------

    def presentation_for(self, scope: MemoryScope) -> str:
        selected: str | None = None
        if self.presentation_provider is not None:
            try:
                selected = self.presentation_provider(scope)
            except Exception:
                selected = None
        value = str(selected or self.default_presentation)
        return value if value in PRESENTATIONS else self.default_presentation

    def _timezone_name(self, scope: MemoryScope) -> str:
        if self.timezone_resolver is not None:
            try:
                return str(self.timezone_resolver.get(scope.user_id))
            except Exception:
                pass
        return str(self.settings.default_timezone or "Asia/Shanghai")

    def _stage(self, scope: MemoryScope) -> str:
        if self.relationship_provider is None:
            return "warming"
        try:
            return str(self.relationship_provider(scope).stage or "warming")
        except Exception:
            return "warming"

    def _valid_mood_hint(
        self,
        previous: PortraitState | None,
        now: datetime,
    ) -> tuple[str | None, datetime | None]:
        if previous is None or not previous.mood_hint or not previous.mood_hint_at:
            return None, None
        observed = _aware(previous.mood_hint_at)
        ttl = timedelta(hours=max(1, int(self.settings.portrait_mood_ttl_hours or 6)))
        if now - observed > ttl:
            return None, None
        return previous.mood_hint, observed

    def _latest_sleep_score(self, user_id: str, local_now: datetime) -> int | None:
        if self.sleep_score_store is None:
            return None
        try:
            from_day = (local_now.date() - timedelta(days=2)).isoformat()
            scores = self.sleep_score_store.list_scores(
                user_id, from_day, local_now.date().isoformat()
            )
        except Exception:
            return None
        for score in reversed(scores):
            health = getattr(getattr(score, "sleep_health", None), "score", None)
            recovery = getattr(getattr(score, "recovery", None), "score", None)
            value = health if health is not None else recovery
            if value is not None:
                return int(value)
        return None

    def _latest_activity(self, user_id: str, now: datetime) -> str | None:
        if self.health_store is None:
            return None
        try:
            sample = self.health_store.latest_sample(user_id, "WORKOUT")
        except Exception:
            return None
        if sample is None:
            return None
        end_at = _parse_dt(sample.bucket_end)
        if end_at is None:
            return None
        window = timedelta(
            minutes=max(1, int(self.settings.portrait_active_window_minutes or 30))
        )
        if now - end_at > window:
            return None
        return _classify_workout(sample.value4)

    def fallback_state(self, scope: MemoryScope, *, now: datetime | None = None) -> PortraitState:
        current = _aware(now or _utcnow())
        zone = _local_zone(self._timezone_name(scope))
        local_now = current.astimezone(zone)
        return PortraitState(
            variant=DEFAULT_VARIANT,
            presentation=self.presentation_for(scope),
            time_slot=resolve_time_slot(local_now.hour),
            mood="neutral",
            stage=self._stage(scope),
            reason="portrait_disabled_fallback",
            resolved_at=current,
        )

    def current(self, scope: MemoryScope, *, now: datetime | None = None) -> PortraitState:
        current = _aware(now or _utcnow())
        previous = self.store.get_portrait_state(scope.user_id, scope.agent_id)
        zone = _local_zone(self._timezone_name(scope))
        local_now = current.astimezone(zone)
        time_slot = resolve_time_slot(local_now.hour)
        mood_hint, mood_hint_at = self._valid_mood_hint(previous, current)
        sleep_score = self._latest_sleep_score(scope.user_id, local_now)
        activity_state = self._latest_activity(scope.user_id, current)
        mood = resolve_mood(
            sleep_score=sleep_score,
            activity_state=activity_state,
            mood_hint=mood_hint,
            local_hour=local_now.hour,
            tired_threshold=max(1, int(self.settings.portrait_sleep_tired_threshold or 60)),
        )
        stage = self._stage(scope)
        presentation = self.presentation_for(scope)
        recent: list[str] = []
        if previous is not None and isinstance(previous.signals, dict):
            raw_recent = previous.signals.get("recent_variants")
            if isinstance(raw_recent, list):
                recent = [str(item) for item in raw_recent]
        variant, reason, signals = pick_variant(
            presentation=presentation,
            time_slot=time_slot,
            mood=mood,
            stage=stage,
            recent_variants=recent,
        )
        effective_mood = "cozy" if variant == "night_cozy" else mood
        history = (recent + [variant])[-6:]
        signals.update(
            {
                "mood": effective_mood,
                "sleep_score": sleep_score,
                "activity_state": activity_state,
                "mood_hint": mood_hint,
                "recent_variants": history,
            }
        )
        state = PortraitState(
            variant=variant,
            presentation=presentation,
            time_slot=time_slot,
            mood=effective_mood,
            stage=stage,
            reason=reason,
            mood_hint=mood_hint,
            mood_hint_at=mood_hint_at,
            signals=signals,
            resolved_at=current,
        )
        self.store.save_portrait_state(scope.user_id, scope.agent_id, state)
        return state

    def image_urls(self, state: PortraitState) -> tuple[str, str, str]:
        return resolve_images(
            state.presentation,
            state.variant,
            static_root=self.static_root,
        )

    def urls_for(self, presentation: str, variant: str) -> tuple[str, str]:
        effective, large, small = resolve_images(
            presentation,
            variant,
            static_root=self.static_root,
        )
        return large, small

    def avatar_url(self, presentation: str) -> str:
        """Resolve the character avatar, or an empty string when unavailable."""

        return resolve_avatar(presentation, static_root=self.static_root)

    # -- observation hook ------------------------------------------------

    def observe_user_text(
        self,
        scope: MemoryScope,
        text: str,
        *,
        now: datetime | None = None,
    ) -> None:
        if not self.can_use_characters(scope):
            return
        mood = mood_from_text(text)
        if mood is None:
            return
        self.store.save_mood_hint(
            scope.user_id,
            scope.agent_id,
            mood,
            _aware(now or _utcnow()),
            presentation=self.presentation_for(scope),
        )


__all__ = [
    "PortraitService",
    "avatar_url",
    "image_url",
    "image_url_small",
    "mood_from_text",
    "pick_variant",
    "resolve_mood",
    "resolve_time_slot",
]
