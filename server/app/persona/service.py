from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import Settings
from app.memory.models import MemoryScope
from app.persona.card import (
    DEFAULT_PRESET_ID,
    apply_overrides,
    apply_presentation,
    builtin_presets,
    load_characters,
    load_presets,
)
from app.persona.models import (
    FREQUENCY_DAILY_LIMITS,
    PRESENTATIONS,
    AgentNote,
    CharacterCard,
    BehaviorPolicy,
    OpenLoop,
    PersonaOverrides,
    PersonaPreset,
    RelationshipState,
    UserPersonaSelection,
    normalize_style,
)
from app.persona.policy import (
    build_behavior_policy,
    extract_question_summary,
    has_commitment,
    infer_relationship_updates,
)
from app.persona.prompt import (
    chat_behavior_block,
    proactive_behavior_block,
    relationship_block,
    stable_persona_block,
)
from app.persona.store import PersonaStore


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_csv(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in str(value).split(",") if item.strip()}


class PersonaService:
    """Resolves the active persona, relationship state and per-turn behavior."""

    def __init__(
        self,
        *,
        store: PersonaStore,
        settings: Settings,
        presets_dir: Path | None = None,
        characters_dir: Path | None = None,
    ) -> None:
        self.store = store
        self.settings = settings
        self.presets = load_presets(
            presets_dir or self._default_presets_dir(),
            default_id=settings.persona_default_preset or DEFAULT_PRESET_ID,
        )
        self.characters = load_characters(
            characters_dir or self._default_characters_dir()
        )
        self._canary = _parse_csv(settings.persona_canary_user_ids)
        self._portrait_service: Any = None

    @staticmethod
    def _default_presets_dir() -> Path:
        return Path(__file__).resolve().parent / "presets"

    @staticmethod
    def _default_characters_dir() -> Path:
        return Path(__file__).resolve().parent / "characters"

    def attach_portrait_service(self, portrait_service: Any) -> None:
        """Wire the smart-background service after both are constructed."""

        self._portrait_service = portrait_service

    def resolve_presentation(self, scope: MemoryScope) -> str | None:
        if not self.settings.persona_user_selection_enabled:
            return None
        selection = self.store.get_user_persona(scope.user_id, scope.agent_id)
        if selection is None:
            return None
        value = selection.overrides.presentation
        return value if value in PRESENTATIONS else None

    def active_character(self, scope: MemoryScope) -> CharacterCard | None:
        """Return the character card only when the portrait rollout reaches this user."""

        if self._portrait_service is None:
            return None
        try:
            if not self._portrait_service.can_use_characters(scope):
                return None
        except Exception:
            return None
        presentation = (
            self.resolve_presentation(scope)
            or str(self.settings.portrait_default_presentation or "female")
        )
        return self.characters.get(presentation)

    def is_enabled(self, scope: MemoryScope) -> bool:
        if not self.settings.persona_enabled:
            return False
        if self._canary and scope.user_id not in self._canary:
            return False
        return True

    def list_presets(self) -> list[dict[str, Any]]:
        result = []
        for preset in sorted(self.presets.values(), key=lambda item: item.id):
            payload = preset.model_dump()
            if not payload.get("label"):
                payload["label"] = preset.id
            result.append(payload)
        return result

    def list_characters(self) -> list[dict[str, Any]]:
        result = []
        for presentation in PRESENTATIONS:
            card = self.characters.get(presentation)
            if card is None:
                continue
            result.append(
                {
                    "presentation": card.presentation,
                    "label": card.label or card.presentation,
                    "display_name": card.display_name or "Auri",
                    "summary": card.summary,
                }
            )
        return result

    def has_user_selection(self, scope: MemoryScope) -> bool:
        return self.store.get_user_persona(scope.user_id, scope.agent_id) is not None

    def default_presentation(self) -> str:
        configured = str(self.settings.portrait_default_presentation or "female")
        return configured if configured in PRESENTATIONS else PRESENTATIONS[0]

    def effective_user_selection(self, scope: MemoryScope) -> UserPersonaSelection:
        enabled = bool(self.settings.persona_user_selection_enabled)
        saved = self.store.get_user_persona(scope.user_id, scope.agent_id) if enabled else None
        default_id = self.settings.persona_default_preset or DEFAULT_PRESET_ID
        default_preset = self.presets.get(default_id) or builtin_presets()[DEFAULT_PRESET_ID]
        presentation = saved.overrides.presentation if saved is not None else None
        if presentation not in PRESENTATIONS:
            presentation = self.default_presentation()
        if saved is None:
            return UserPersonaSelection(
                preset_id=default_preset.id,
                overrides=PersonaOverrides(presentation=presentation),
            )
        overrides = saved.overrides.model_copy(deep=True)
        overrides.presentation = presentation
        return saved.model_copy(update={"overrides": overrides})

    def resolve_preset(self, scope: MemoryScope) -> PersonaPreset:
        default_id = self.settings.persona_default_preset or DEFAULT_PRESET_ID
        fallback = self.presets.get(default_id) or builtin_presets()[DEFAULT_PRESET_ID]
        selection = (
            self.store.get_user_persona(scope.user_id, scope.agent_id)
            if self.settings.persona_user_selection_enabled
            else None
        )
        if selection is None:
            preset = fallback
        else:
            base = self.presets.get(selection.preset_id) or fallback
            preset = apply_overrides(base, selection.overrides)
        return apply_presentation(preset, self.active_character(scope))

    def relationship_state(self, scope: MemoryScope) -> RelationshipState:
        state = self.store.get_relationship_state(scope.user_id, scope.agent_id)
        if state is not None:
            return state
        return RelationshipState()

    def open_loop_count(self, scope: MemoryScope) -> int:
        return self.store.count_open_loops(scope.user_id, scope.agent_id)

    def frequency_preset(self, scope: MemoryScope) -> str:
        if not self.is_enabled(scope):
            return self.settings.proactive_frequency_preset or "high"
        preset = self.resolve_preset(scope)
        return (
            preset.proactive_frequency_preset
            or self.settings.proactive_frequency_preset
            or "high"
        )

    def daily_limit(self, scope: MemoryScope) -> int:
        frequency = self.frequency_preset(scope)
        return int(
            FREQUENCY_DAILY_LIMITS.get(
                frequency,
                self.settings.proactive_daily_message_limit,
            )
        )

    def build_behavior_policy(
        self,
        scope: MemoryScope,
        user_text: str,
        planned_style: str,
        *,
        has_attachment: bool = False,
        allow_silent: bool = True,
    ) -> BehaviorPolicy:
        preset = self.resolve_preset(scope)
        relationship = self.relationship_state(scope)
        return build_behavior_policy(
            preset,
            relationship,
            user_text,
            planned_style,
            has_attachment=has_attachment,
            allow_silent=allow_silent,
            open_loop_count=self.open_loop_count(scope),
        )

    def build_chat_blocks(
        self,
        scope: MemoryScope,
        user_text: str,
        planned_style: str,
        *,
        has_attachment: bool = False,
        allow_silent: bool = True,
        policy_override: dict[str, Any] | None = None,
    ) -> tuple[str, str, str]:
        """Return persona, relationship and behavior blocks for one chat turn."""

        if not self.is_enabled(scope):
            return "", "", ""
        preset = self.resolve_preset(scope)
        relationship = self.relationship_state(scope)
        if policy_override:
            allowed = {
                key: value
                for key, value in policy_override.items()
                if key in BehaviorPolicy.model_fields
            }
            policy = BehaviorPolicy(**allowed)
            if not policy.persona_id or policy.persona_id == "warm_friend":
                policy.persona_id = preset.id
            policy.open_loop_count = self.open_loop_count(scope)
        else:
            policy = build_behavior_policy(
                preset,
                relationship,
                user_text,
                planned_style,
                has_attachment=has_attachment,
                allow_silent=allow_silent,
                open_loop_count=self.open_loop_count(scope),
            )
        return (
            stable_persona_block(preset, self.active_character(scope)),
            relationship_block(
                relationship,
                open_loop_count=policy.open_loop_count,
            ),
            chat_behavior_block(policy),
        )

    def render_persona_prompt(self, scope: MemoryScope) -> str:
        if not self.is_enabled(scope):
            return ""
        preset = self.resolve_preset(scope)
        relationship = self.relationship_state(scope)
        blocks = [
            stable_persona_block(preset, self.active_character(scope)),
            relationship_block(
                relationship,
                open_loop_count=self.open_loop_count(scope),
            ),
        ]
        return "\n\n".join(block for block in blocks if block)

    def build_proactive_context(
        self,
        scope: MemoryScope,
        *,
        daily_count: int,
        remaining: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        if not self.is_enabled(scope):
            return {
                "persona_prompt": "",
                "relationship_prompt": "",
                "behavior_prompt": "",
                "frequency_preset": self.settings.proactive_frequency_preset or "high",
                "daily_limit": self.settings.proactive_daily_message_limit,
            }
        preset = self.resolve_preset(scope)
        relationship = self.relationship_state(scope)
        frequency = (
            preset.proactive_frequency_preset
            or self.settings.proactive_frequency_preset
            or "high"
        )
        limit = int(
            FREQUENCY_DAILY_LIMITS.get(
                frequency, self.settings.proactive_daily_message_limit
            )
        )
        open_loops = self.open_loop_count(scope)
        return {
            "persona_prompt": stable_persona_block(preset, self.active_character(scope)),
            "relationship_prompt": relationship_block(
                relationship,
                open_loop_count=open_loops,
            ),
            "behavior_prompt": proactive_behavior_block(
                frequency_preset=frequency,
                daily_limit=limit,
                daily_count=daily_count,
                open_loop_count=open_loops,
                remaining=remaining,
            ),
            "frequency_preset": frequency,
            "daily_limit": limit,
        }

    def update_user_preferences(
        self,
        scope: MemoryScope,
        *,
        preset_id: str | None = None,
        presentation: str | None = None,
    ) -> UserPersonaSelection:
        if not self.settings.persona_user_selection_enabled:
            raise ValueError("user persona selection is disabled")
        current = self.effective_user_selection(scope)
        next_preset_id = preset_id or current.preset_id
        if next_preset_id not in self.presets:
            raise ValueError(f"unknown persona preset: {next_preset_id}")
        next_presentation = current.overrides.presentation or self.default_presentation()
        if presentation is not None:
            if presentation not in PRESENTATIONS:
                raise ValueError(f"unknown presentation: {presentation}")
            next_presentation = presentation
        existing = self.store.get_user_persona(scope.user_id, scope.agent_id)
        now = _utcnow()
        overrides = current.overrides.model_copy(deep=True)
        overrides.presentation = next_presentation
        selection = UserPersonaSelection(
            preset_id=next_preset_id,
            overrides=overrides,
            selected_at=existing.selected_at if existing is not None else now,
            updated_at=now,
        )
        self.store.set_user_persona(scope.user_id, scope.agent_id, selection)
        return selection

    def set_user_selection(
        self,
        scope: MemoryScope,
        preset_id: str,
        overrides: dict[str, Any] | None = None,
    ) -> UserPersonaSelection:
        if preset_id not in self.presets:
            raise ValueError(f"unknown persona preset: {preset_id}")
        selection = UserPersonaSelection(
            preset_id=preset_id,
            overrides=PersonaOverrides(**(overrides or {})),
        )
        self.store.set_user_persona(scope.user_id, scope.agent_id, selection)
        return selection

    def on_user_message(self, scope: MemoryScope, text: str) -> None:
        if not self.is_enabled(scope):
            return
        self.store.expire_open_loops(scope.user_id, scope.agent_id)
        self.store.close_latest_open_loop(scope.user_id, scope.agent_id)
        if self._portrait_service is not None:
            try:
                self._portrait_service.observe_user_text(scope, text)
            except Exception:
                pass
        updates = infer_relationship_updates(text)
        if not updates:
            return
        state = self.relationship_state(scope)
        if "address" in updates:
            state.address = str(updates["address"])[:32]
        if "tone" in updates:
            state.tone = str(updates["tone"])  # type: ignore[assignment]
        boundaries = updates.get("boundaries")
        if isinstance(boundaries, list):
            for boundary in boundaries:
                value = str(boundary).strip()
                if value and value not in state.boundaries:
                    state.boundaries.append(value)
            state.boundaries = state.boundaries[-12:]
        if self._looks_like_correction(text):
            state.last_correction_at = _utcnow()
            state.recent_correction_count = min(
                10, (state.recent_correction_count or 0) + 1
            )
        state.updated_at = _utcnow()
        self.store.save_relationship_state(scope.user_id, scope.agent_id, state)

    def on_assistant_message(
        self,
        scope: MemoryScope,
        text: str,
        *,
        source: str = "chat",
        source_ref: str | None = None,
    ) -> None:
        if not self.is_enabled(scope):
            return
        summary = extract_question_summary(text)
        if summary and self.open_loop_count(scope) == 0:
            self.store.add_open_loop(
                scope.user_id,
                scope.agent_id,
                OpenLoop(
                    kind="question",
                    summary=summary,
                    source="chat" if source == "chat" else "proactive",
                    source_ref=source_ref,
                    expires_at=self.store.open_loop_expiry(),
                ),
            )
        if has_commitment(text):
            self.store.add_agent_note(
                scope.user_id,
                scope.agent_id,
                AgentNote(
                    kind="commitment",
                    summary=text.strip()[:200],
                    source_ref=source_ref,
                    expires_at=self.store.open_loop_expiry(important=True),
                ),
            )

    def wants_less_questions(self, scope: MemoryScope) -> bool:
        if not self.is_enabled(scope):
            return False
        state = self.relationship_state(scope)
        combined = "；".join(state.boundaries)
        return any(
            marker in combined
            for marker in ("少提问", "降低主动频率", "先安静", "别问")
        )

    @staticmethod
    def _looks_like_correction(text: str) -> bool:
        raw = text or ""
        return any(
            marker in raw
            for marker in (
                "不是",
                "错了",
                "刚说过",
                "失忆",
                "你搞错",
                "我说的是",
                "别乱说",
            )
        )