from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.health.types import ALL_METRIC_TYPES
from app.reminders.models import Reminder, ReminderKind
from app.reminders.store import ReminderStore


_CLOCK_RE = re.compile(r"^(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?$")
_DAY_CLOCK_RE = re.compile(
    r"^(?P<day>today|tomorrow)\s*(?:at\s+)?"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?$"
)
_AT_CLOCK_RE = re.compile(
    r"^(?:at\s+)(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?$"
)
_ABSOLUTE_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"(?:[ T](?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?)?$"
)
_RELATIVE_RE = re.compile(r"^in\s+(?P<count>\d+)\s*(?P<unit>minutes?|mins?|hours?|hrs?|days?)$")


def _clock_dt(text: str, base: datetime) -> datetime | None:
    match = _CLOCK_RE.match(text)
    if not match:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    second = int(match.group("second") or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return base.replace(hour=hour, minute=minute, second=second, microsecond=0)


def parse_when(text: str, tz: ZoneInfo) -> datetime:
    """Parse a natural time expression into an aware datetime in ``tz``."""
    value = (text or "").strip().lower()
    if not value:
        raise ValueError("'when' must not be empty.")

    now = datetime.now(tz)

    if value == "now":
        return now

    relative = _RELATIVE_RE.match(value)
    if relative:
        count = int(relative.group("count"))
        unit = relative.group("unit")
        if unit.startswith("min"):
            return now + timedelta(minutes=count)
        if unit.startswith("hour") or unit.startswith("hr"):
            return now + timedelta(hours=count)
        return now + timedelta(days=count)

    absolute = _ABSOLUTE_RE.match(value)
    if absolute:
        year = int(absolute.group("year"))
        month = int(absolute.group("month"))
        day = int(absolute.group("day"))
        hour = int(absolute.group("hour") or 0)
        minute = int(absolute.group("minute") or 0)
        second = int(absolute.group("second") or 0)
        try:
            return datetime(year, month, day, hour, minute, second, tzinfo=tz)
        except ValueError as exc:
            raise ValueError(f"Invalid date/time '{text}'.") from exc

    day_clock = _DAY_CLOCK_RE.match(value)
    if day_clock:
        base_day = now.date()
        if day_clock.group("day") == "tomorrow":
            base_day += timedelta(days=1)
        clock = _clock_dt(
            f"{day_clock.group('hour')}:{day_clock.group('minute')}"
            f"{':' + day_clock.group('second') if day_clock.group('second') else ''}",
            datetime(base_day.year, base_day.month, base_day.day, tzinfo=tz),
        )
        if clock is None:
            raise ValueError(f"Invalid time expression '{text}'.")
        return clock

    at_clock = _AT_CLOCK_RE.match(value)
    clock_text = value
    if at_clock:
        clock_text = (
            f"{at_clock.group('hour')}:{at_clock.group('minute')}"
            f"{':' + at_clock.group('second') if at_clock.group('second') else ''}"
        )
    clock = _clock_dt(clock_text, now.replace(microsecond=0))
    if clock is not None:
        if clock <= now:
            clock += timedelta(days=1)
        return clock

    raise ValueError(
        f"Could not parse time '{text}'. Use a form like 'in 30 minutes', "
        "'tomorrow 08:00', 'today 21:30', '08:00', or '2026-08-24 09:30'."
    )


def _resolve_tz(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


class ReminderService:
    """Create, list, and cancel reminders for one user."""

    def __init__(
        self,
        store: ReminderStore,
        timezone: str = "Asia/Shanghai",
        timezone_resolver: Callable[[str], str] | None = None,
    ) -> None:
        self.store = store
        self.timezone = timezone
        self.tz = _resolve_tz(timezone)
        self.timezone_resolver = timezone_resolver

    def create_time(
        self,
        user_id: str,
        agent_id: str,
        message: str,
        when: str,
        tz: str | None = None,
    ) -> Reminder:
        if tz:
            zone = _resolve_tz(tz)
        elif self.timezone_resolver is not None:
            zone = _resolve_tz(self.timezone_resolver(user_id))
        else:
            zone = self.tz
        due_at = parse_when(when, zone)
        reminder = Reminder(
            user_id=user_id,
            agent_id=agent_id,
            kind=ReminderKind.time,
            message=message,
            due_at=due_at,
        )
        return self.store.save(reminder)

    def create_event(
        self,
        user_id: str,
        agent_id: str,
        message: str,
        metric_type: str,
        operator: str,
        threshold: float,
    ) -> Reminder:
        if metric_type not in ALL_METRIC_TYPES:
            raise ValueError(
                f"Unknown metric_type '{metric_type}'. Must be one of "
                f"{', '.join(sorted(ALL_METRIC_TYPES))}."
            )
        if operator not in {"<", "<=", ">", ">=", "=="}:
            raise ValueError(
                f"Unknown operator '{operator}'. Must be one of <, <=, >, >=, ==."
            )
        reminder = Reminder(
            user_id=user_id,
            agent_id=agent_id,
            kind=ReminderKind.event,
            message=message,
            metric_type=metric_type,
            operator=operator,
            threshold=float(threshold),
        )
        return self.store.save(reminder)

    def list(self, user_id: str, agent_id: str = "default") -> list[Reminder]:
        return self.store.list(user_id, agent_id)

    def cancel(self, reminder_id: str, user_id: str, agent_id: str = "default") -> bool:
        return self.store.delete(reminder_id, user_id, agent_id)
