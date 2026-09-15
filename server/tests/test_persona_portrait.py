from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.config import Settings
from app.memory.models import MemoryScope
from app.persona.card import apply_presentation, builtin_characters, builtin_presets
from app.persona.models import PersonaOverrides, PortraitState
from app.persona.portrait import (
    VARIANTS,
    image_url,
    image_url_small,
    pick_variant,
    resolve_images,
    resolve_mood,
    resolve_time_slot,
)
from app.persona.portrait_service import PortraitService, mood_from_text
from app.persona.service import PersonaService
from app.persona.store import PersonaStore


UTC = timezone.utc
# 2026-09-15 12:00 UTC == 20:00 Asia/Shanghai (night slot).
NIGHT_UTC = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
# 2026-09-15 00:00 UTC == 08:00 Asia/Shanghai (dawn slot).
DAWN_UTC = datetime(2026, 9, 15, 0, 0, tzinfo=UTC)


def _settings(tmp_dir, **overrides) -> Settings:
    values = {
        "data_dir": tmp_dir,
        "portrait_enabled": True,
        "portrait_default_presentation": "female",
    }
    values.update(overrides)
    return Settings(**values)


def _store(tmp_dir) -> PersonaStore:
    return PersonaStore(tmp_dir / "memory.db")


def _service(tmp_dir, *, settings=None, **kwargs) -> PortraitService:
    return PortraitService(
        store=_store(tmp_dir),
        settings=settings or _settings(tmp_dir),
        **kwargs,
    )


class _PortraitGate:
    def can_use_characters(self, scope) -> bool:
        return True


class _SleepStore:
    def __init__(self, score):
        self.score = score

    def list_scores(self, user_id, from_day, to_day):
        if self.score is None:
            return []
        dimension = SimpleNamespace(score=self.score)
        return [SimpleNamespace(sleep_health=dimension, recovery=dimension)]


class _HealthStore:
    def __init__(self, activity_type=None, end_at=None):
        self.activity_type = activity_type
        self.end_at = end_at

    def latest_sample(self, user_id, sample_type):
        if self.activity_type is None:
            return None
        return SimpleNamespace(value4=self.activity_type, bucket_end=self.end_at)


# --- pure resolver ---------------------------------------------------------


def test_time_slot_boundaries() -> None:
    assert resolve_time_slot(4) == "night"
    assert resolve_time_slot(5) == "dawn"
    assert resolve_time_slot(8) == "dawn"
    assert resolve_time_slot(9) == "day"
    assert resolve_time_slot(16) == "day"
    assert resolve_time_slot(17) == "dusk"
    assert resolve_time_slot(19) == "dusk"
    assert resolve_time_slot(20) == "night"
    assert resolve_time_slot(0) == "night"


def test_mood_priority_is_low_then_tired_then_active_then_cheerful() -> None:
    assert resolve_mood(mood_hint="low", sleep_score=20, activity_state="步行") == "low"
    assert resolve_mood(sleep_score=45, local_hour=8) == "tired"
    assert resolve_mood(sleep_score=45, local_hour=18) == "neutral"
    assert resolve_mood(activity_state="骑行") == "active"
    assert resolve_mood(mood_hint="cheerful") == "cheerful"
    assert resolve_mood() == "neutral"


def test_pick_variant_state_priority_and_time_defaults() -> None:
    assert pick_variant(presentation="female", time_slot="day", mood="low", stage="close")[0] == "sad_low"
    assert pick_variant(presentation="female", time_slot="day", mood="tired", stage="close")[0] == "tired_rest"
    assert pick_variant(presentation="male", time_slot="night", mood="active", stage="close")[0] == "day_active"
    assert pick_variant(presentation="male", time_slot="night", mood="cheerful", stage="close")[0] == "celebrate_up"
    assert pick_variant(presentation="female", time_slot="night", mood="neutral", stage="close")[0] == "night_cozy"
    assert pick_variant(presentation="female", time_slot="night", mood="neutral", stage="new")[0] == "night_quiet"
    assert pick_variant(presentation="female", time_slot="dawn", mood="neutral", stage="close")[0] == "dawn_calm"
    assert pick_variant(presentation="male", time_slot="day", mood="neutral", stage="close")[0] == "day_gentle"
    assert pick_variant(presentation="male", time_slot="dusk", mood="neutral", stage="close")[0] == "dusk_warm"


def test_night_cozy_rotates_after_two_consecutive_uses() -> None:
    variant, reason, _ = pick_variant(
        presentation="female",
        time_slot="night",
        mood="neutral",
        stage="familiar",
        recent_variants=["night_cozy", "night_cozy"],
    )
    assert (variant, reason) == ("late_study", "night_cozy_rotation")
    variant, reason, _ = pick_variant(
        presentation="female",
        time_slot="night",
        mood="neutral",
        stage="familiar",
        recent_variants=["night_cozy", "night_cozy", "late_study"],
    )
    assert (variant, reason) == ("night_quiet", "night_cozy_rotation")


def test_image_urls_and_missing_file_fallback(tmp_dir) -> None:
    root = tmp_dir / "static"
    (root / "female").mkdir(parents=True)
    (root / "female" / "day_gentle.jpg").write_bytes(b"x")
    effective, large, small = resolve_images(
        "female", "sad_low", static_root=root
    )
    assert effective == "day_gentle"
    assert large == image_url("female", "day_gentle")
    assert small == image_url_small("female", "day_gentle")


# --- character cards -------------------------------------------------------


def test_character_cards_layer_on_preset_without_touching_base() -> None:
    cards = builtin_characters()
    assert set(cards) == {"female", "male"}
    base = builtin_presets()["warm_friend"]
    layered = apply_presentation(base, cards["male"])
    assert layered.emoji_level == "off"
    assert any("行" in item for item in layered.speech_style)
    assert "骑行" in layered.interests
    # The base preset object is unchanged.
    assert base.emoji_level == "low"
    assert "骑行" not in base.interests


def test_presentation_override_selects_character(tmp_dir) -> None:
    persona = PersonaService(store=_store(tmp_dir), settings=_settings(tmp_dir))
    persona.attach_portrait_service(_PortraitGate())
    scope = MemoryScope(user_id="u1")
    persona.set_user_selection(scope, "warm_friend", {"presentation": "male"})
    assert persona.resolve_presentation(scope) == "male"
    preset = persona.resolve_preset(scope)
    assert preset.emoji_level == "off"
    assert "机械键盘" in preset.interests
    block = persona.build_chat_blocks(scope, "在吗", "short")[0]
    assert "形象：Auri（男）" in block


def test_invalid_presentation_is_rejected() -> None:
    with pytest.raises(Exception):
        PersonaOverrides(presentation="other")  # type: ignore[arg-type]


# --- store and service -----------------------------------------------------


def test_store_round_trips_portrait_state_and_settings(tmp_dir) -> None:
    store = _store(tmp_dir)
    scope = MemoryScope(user_id="u1")
    state = PortraitState(
        variant="sad_low",
        presentation="male",
        time_slot="day",
        mood="low",
        stage="close",
        reason="recent_low_mood",
        mood_hint="low",
        mood_hint_at=NIGHT_UTC,
        signals={"mood": "low", "recent_variants": ["sad_low"]},
        resolved_at=NIGHT_UTC,
    )
    store.save_portrait_state(scope.user_id, scope.agent_id, state)
    loaded = store.get_portrait_state(scope.user_id, scope.agent_id)
    assert loaded is not None
    assert loaded.variant == "sad_low"
    assert loaded.mood_hint == "low"
    assert loaded.signals["recent_variants"] == ["sad_low"]
    assert store.get_portrait_settings(scope.user_id).smart_background_enabled is True

    preference = store.get_portrait_settings(scope.user_id)
    preference.smart_background_enabled = False
    store.set_portrait_settings(scope.user_id, scope.agent_id, preference)
    assert store.get_portrait_settings(scope.user_id).smart_background_enabled is False
    store.delete_user(scope.user_id, scope.agent_id)
    assert store.get_portrait_state(scope.user_id, scope.agent_id) is None


def test_service_uses_sleep_and_activity_signals(tmp_dir) -> None:
    scope = MemoryScope(user_id="u1")
    service = _service(
        tmp_dir,
        sleep_score_store=_SleepStore(42),
        health_store=_HealthStore(),
    )
    state = service.current(scope, now=DAWN_UTC)
    assert state.variant == "tired_rest"
    assert state.reason == "low_sleep_score"

    active = _service(
        tmp_dir,
        sleep_score_store=_SleepStore(None),
        health_store=_HealthStore("outdoor_riding", NIGHT_UTC.isoformat()),
    )
    state = active.current(scope, now=NIGHT_UTC)
    assert state.mood == "active"
    assert state.variant == "day_active"


def test_mood_hint_from_text_has_ttl(tmp_dir) -> None:
    assert mood_from_text("今天好累") == "low"
    assert mood_from_text("今天太开心了") == "cheerful"
    assert mood_from_text("今天吃了拉面") is None

    scope = MemoryScope(user_id="u1")
    service = _service(tmp_dir)
    service.observe_user_text(scope, "今天真的好累", now=NIGHT_UTC)
    state = service.current(scope, now=NIGHT_UTC)
    assert state.variant == "sad_low"
    assert state.mood_hint == "low"

    stale = service.current(scope, now=NIGHT_UTC + timedelta(hours=7))
    assert stale.mood_hint is None
    assert stale.variant == "night_quiet"


def test_service_respects_user_switch_and_canary(tmp_dir) -> None:
    scope = MemoryScope(user_id="u1")
    service = _service(tmp_dir)
    assert service.is_enabled(scope) is True
    service.update_settings(scope, smart_background_enabled=False)
    assert service.is_enabled(scope) is False

    canary = _service(
        tmp_dir,
        settings=_settings(tmp_dir, portrait_canary_user_ids="vip@example.com"),
    )
    assert canary.feature_available(scope) is False
    assert canary.feature_available(MemoryScope(user_id="vip@example.com")) is True


def test_character_layer_requires_portrait_rollout(tmp_dir) -> None:
    persona = PersonaService(store=_store(tmp_dir), settings=_settings(tmp_dir))
    scope = MemoryScope(user_id="u1")
    # Without an attached portrait service, behavior is unchanged.
    assert persona.resolve_preset(scope).emoji_level == "low"

    disabled = _service(
        tmp_dir, settings=_settings(tmp_dir, portrait_enabled=False)
    )
    persona.attach_portrait_service(disabled)
    assert persona.resolve_preset(scope).emoji_level == "low"


# --- API -------------------------------------------------------------------


@pytest.fixture
def client(tmp_dir, monkeypatch):
    monkeypatch.setenv("AURI_DATA_DIR", str(tmp_dir))
    monkeypatch.setenv("AURI_LLM_PROVIDER", "echo")
    monkeypatch.setenv("AURI_PROACTIVE_QUIET_HOURS", "")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_portrait_api_requires_auth_and_respects_switch(client) -> None:
    assert client.get("/v1/portrait/current").status_code == 401

    container = client.app.state.container
    container.settings.portrait_enabled = True
    container.settings.portrait_canary_user_ids = ""
    container.portrait_service._canary = set()
    container.settings.portrait_default_presentation = "female"

    email = f"portrait-{uuid4().hex[:8]}@example.com"
    register = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "test1234"},
    )
    assert register.status_code == 201, register.text
    headers = {"Authorization": f"Bearer {register.json()['token']}"}

    current = client.get("/v1/portrait/current", headers=headers)
    assert current.status_code == 200, current.text
    payload = current.json()
    assert payload["enabled"] is True
    assert payload["presentation"] == "female"
    assert payload["variant"] in VARIANTS
    assert payload["image_url"].startswith("/static/portrait/female/")
    assert payload["expires_in_seconds"] >= 60

    settings = client.get("/v1/portrait/settings", headers=headers).json()
    assert settings == {"smart_background_enabled": True, "available": True}

    disabled = client.put(
        "/v1/portrait/settings",
        json={"smart_background_enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["smart_background_enabled"] is False

    after = client.get("/v1/portrait/current", headers=headers).json()
    assert after["enabled"] is False
    assert after["variant"] == "day_gentle"

    restored = client.put(
        "/v1/portrait/settings",
        json={"smart_background_enabled": True},
        headers=headers,
    )
    assert restored.json()["smart_background_enabled"] is True
