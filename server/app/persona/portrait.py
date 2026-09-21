from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence

TIME_SLOTS: tuple[str, ...] = ("dawn", "day", "dusk", "night")
MOODS: tuple[str, ...] = ("neutral", "tired", "low", "active", "cozy", "cheerful")
VARIANTS: tuple[str, ...] = (
    "dawn_calm",
    "day_focus",
    "day_active",
    "day_gentle",
    "dusk_warm",
    "dusk_lonely",
    "night_cozy",
    "night_quiet",
    "late_study",
    "tired_rest",
    "sad_low",
    "celebrate_up",
    "summer_light",
    "autumn_wind",
    "rain_umbrella",
    "snow_winter",
)
DEFAULT_VARIANT = "day_gentle"
PRESENTATIONS: tuple[str, ...] = ("female", "male")
ACTIVE_ACTIVITY_STATES: tuple[str, ...] = ("步行", "骑行", "跑步", "运动")


WEATHER_VARIANTS: tuple[str, ...] = (
    "summer_light",
    "autumn_wind",
    "rain_umbrella",
    "snow_winter",
)
SNOW_WEATHER_CODES: frozenset[int] = frozenset({71, 73, 75, 77, 85, 86})
RAIN_WEATHER_CODES: frozenset[int] = frozenset(
    {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
)

DEFAULT_STATIC_ROOT = Path(__file__).resolve().parent.parent / "static" / "portrait"
AVATAR_DIRNAME = "avatar"

_TIME_DEFAULTS = {
    "dawn": ("dawn_calm", "dawn_slot_default"),
    "day": ("day_gentle", "day_slot_default"),
    "dusk": ("dusk_warm", "dusk_slot_default"),
    "night": ("night_quiet", "night_slot_default"),
}


def resolve_time_slot(local_hour: int) -> str:
    """Map a local hour (0-23) to one of the four portrait time slots."""

    hour = int(local_hour) % 24
    if 5 <= hour < 9:
        return "dawn"
    if 9 <= hour < 17:
        return "day"
    if 17 <= hour < 20:
        return "dusk"
    return "night"


def resolve_mood(
    *,
    sleep_score: int | None = None,
    steps: int | None = None,
    activity_state: str | None = None,
    mood_hint: str | None = None,
    local_hour: int = 12,
    tired_threshold: int = 60,
) -> str:
    """Resolve an objective mood bucket.

    ``steps`` is accepted so callers can pass the full signal set without
    branching; the deterministic rules intentionally use sleep and fresh
    workout activity as the only body signals.
    """

    hint = str(mood_hint or "").strip().lower()
    if hint == "low":
        return "low"
    if (
        sleep_score is not None
        and int(sleep_score) < int(tired_threshold)
        and 0 <= int(local_hour) % 24 < 17
    ):
        return "tired"
    if activity_state in ACTIVE_ACTIVITY_STATES:
        return "active"
    if hint == "cheerful":
        return "cheerful"
    return "neutral"


def _tail_is(values: Sequence[str], value: str, count: int) -> bool:
    return len(values) >= count and all(item == value for item in values[-count:])


def classify_weather(weather_code: int | None) -> str | None:
    """Map an Open-Meteo WMO weather code to snow / rain / clear."""

    if weather_code is None:
        return None
    try:
        code = int(weather_code)
    except (TypeError, ValueError):
        return None
    if code in SNOW_WEATHER_CODES:
        return "snow"
    if code in RAIN_WEATHER_CODES:
        return "rain"
    return "clear"


def season_for_month(local_month: int, hemisphere: str = "north") -> str:
    """Return the astronomical season for a local month.

    ``hemisphere`` accepts ``north`` or ``south`` so self-hosted instances in
    the southern hemisphere can keep the calendar seasons aligned.
    """

    month = int(local_month) % 12
    if month == 0:
        month = 12
    north = str(hemisphere or "north").strip().lower().startswith("north")
    if month in (3, 4, 5):
        return "spring" if north else "autumn"
    if month in (6, 7, 8):
        return "summer" if north else "winter"
    if month in (9, 10, 11):
        return "autumn" if north else "spring"
    return "winter" if north else "summer"


def _numeric(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def pick_weather_variant(
    *,
    weather: Mapping[str, Any] | None,
    local_month: int | None = None,
    hemisphere: str = "north",
    hot_threshold_c: float = 28.0,
    cold_threshold_c: float = 10.0,
) -> tuple[str | None, str | None, dict[str, object]]:
    """Choose a weather/season variant from the latest observation.

    Rain and snow override mood because they are immediately visible cues the
    user asked to match. Temperature is checked before the calendar season so
    an unusually hot autumn day or a cold summer rain still gets suitable
    clothing.
    """

    weather_code = weather.get("weather_code") if weather else None
    temperature = _numeric(weather.get("temperature_2m")) if weather else None
    kind = classify_weather(weather_code)
    season = (
        season_for_month(local_month, hemisphere)
        if local_month is not None
        else None
    )
    signals: dict[str, object] = {
        "weather_code": weather_code,
        "temperature_c": temperature,
        "weather_kind": kind,
        "season": season,
    }
    if not weather:
        # No observation means the old time/mood behavior stays authoritative.
        return None, None, signals

    if kind == "snow":
        return "snow_winter", "weather_snow", signals
    if kind == "rain":
        return "rain_umbrella", "weather_rain", signals
    if temperature is not None and temperature >= float(hot_threshold_c):
        return "summer_light", "weather_hot", signals
    if temperature is not None and temperature <= float(cold_threshold_c):
        return "autumn_wind", "weather_cold", signals
    if season == "summer":
        return "summer_light", "season_summer", signals
    if season == "autumn":
        return "autumn_wind", "season_autumn", signals
    return None, None, signals


def pick_variant(
    *,
    presentation: str,
    time_slot: str,
    mood: str,
    stage: str,
    recent_variants: Sequence[str] = (),
    weather: Mapping[str, Any] | None = None,
    local_month: int | None = None,
    hemisphere: str = "north",
    hot_threshold_c: float = 28.0,
    cold_threshold_c: float = 10.0,
) -> tuple[str, str, dict[str, object]]:
    """Return ``(variant, reason, signals)`` for one resolved background.

    Weather priority: rain/snow first (immediately visible); low/tired state
    next; then hot/cold and calendar seasons; then activity/cheerfulness and
    the existing time-slot defaults.
    """

    recent = [str(item) for item in recent_variants][-8:]
    signals: dict[str, object] = {
        "presentation": presentation,
        "time_slot": time_slot,
        "mood": mood,
        "stage": stage,
        "recent_variants": recent,
    }
    weather_variant, weather_reason, weather_signals = pick_weather_variant(
        weather=weather,
        local_month=local_month,
        hemisphere=hemisphere,
        hot_threshold_c=hot_threshold_c,
        cold_threshold_c=cold_threshold_c,
    )
    signals.update(weather_signals)

    if weather_variant in ("snow_winter", "rain_umbrella"):
        return weather_variant, weather_reason or "weather", signals
    if mood == "low":
        return "sad_low", "recent_low_mood", signals
    if mood == "tired":
        return "tired_rest", "low_sleep_score", signals
    if weather_variant is not None:
        return weather_variant, weather_reason or "weather", signals
    if mood == "active":
        return "day_active", "fresh_activity", signals
    if mood == "cheerful":
        return "celebrate_up", "recent_cheerful_mood", signals

    if time_slot == "night":
        if stage in ("familiar", "close"):
            rotation_variants = ("late_study", "night_quiet")
            tail_rotation: list[str] = []
            for item in reversed(recent):
                if item in rotation_variants:
                    tail_rotation.insert(0, item)
                else:
                    break
            prefix = recent[: len(recent) - len(tail_rotation)]
            cozy_run = 0
            for item in reversed(prefix):
                if item == "night_cozy":
                    cozy_run += 1
                else:
                    break
            if cozy_run >= 2 or tail_rotation:
                if tail_rotation and tail_rotation[-1] == "night_quiet":
                    variant = "late_study"
                else:
                    variant = "night_quiet" if tail_rotation else "late_study"
                return variant, "night_cozy_rotation", signals
            return "night_cozy", "night_slot_with_close_stage", signals
        return "night_quiet", "night_slot_default", signals

    variant, reason = _TIME_DEFAULTS.get(
        time_slot, ("day_gentle", "day_slot_default")
    )
    return variant, reason, signals

def image_url(presentation: str, variant: str) -> str:
    return f"/static/portrait/{presentation}/{variant}.jpg"


def image_url_small(presentation: str, variant: str) -> str:
    return f"/static/portrait/{presentation}/{variant}@540.jpg"


def avatar_url(presentation: str) -> str:
    """Return the stable avatar URL for one character presentation."""

    value = presentation if presentation in PRESENTATIONS else PRESENTATIONS[0]
    return f"/static/portrait/{AVATAR_DIRNAME}/{value}.jpg"


def resolve_avatar(
    presentation: str,
    *,
    static_root: Path | str | None = None,
) -> str:
    """Return the avatar URL only when the file exists.

    Public placeholder deployments may ship without avatar files; an empty
    string lets clients fall back to the built-in gradient avatar instead of
    requesting a URL that always 404s.
    """

    root = Path(static_root) if static_root is not None else DEFAULT_STATIC_ROOT
    value = presentation if presentation in PRESENTATIONS else PRESENTATIONS[0]
    if (root / AVATAR_DIRNAME / f"{value}.jpg").exists():
        return avatar_url(value)
    return ""


def _existing_variant(
    root: Path,
    presentation: str,
    variant: str,
) -> bool:
    return (root / presentation / f"{variant}.jpg").exists()


def resolve_images(
    presentation: str,
    variant: str,
    *,
    static_root: Path | str | None = None,
) -> tuple[str, str, str]:
    """Resolve the requested variant to a real file.

    Returns ``(effective_variant, image_url, image_url_small)``. If the exact
    portrait file is missing the resolver falls back to ``day_gentle`` and then
    to the first available file for that presentation, so a partially built
    static directory never produces a broken URL.
    """

    root = Path(static_root) if static_root is not None else DEFAULT_STATIC_ROOT
    presentation = presentation if presentation in PRESENTATIONS else PRESENTATIONS[0]
    candidates: list[str] = [variant, DEFAULT_VARIANT, *VARIANTS]
    for candidate in candidates:
        if _existing_variant(root, presentation, candidate):
            return (
                candidate,
                image_url(presentation, candidate),
                image_url_small(presentation, candidate),
            )
    directory = root / presentation
    if directory.is_dir():
        for path in sorted(directory.glob("*.jpg")):
            if path.name.endswith("@540.jpg"):
                continue
            candidate = path.stem
            return (
                candidate,
                image_url(presentation, candidate),
                image_url_small(presentation, candidate),
            )
    return variant, image_url(presentation, variant), image_url_small(
        presentation, variant
    )
