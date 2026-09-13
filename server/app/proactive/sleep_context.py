from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable

from app.services.timezone_store import resolve_zoneinfo


PROFILE_ALGORITHM_VERSION = "sleep_routine_v1"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_time(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _aware(parsed)


@dataclass(frozen=True)
class SleepRoutineProfile:
    user_id: str
    agent_id: str
    timezone: str
    bedtime_center_minute: float
    wake_center_minute: float
    bedtime_spread_minutes: float
    wake_spread_minutes: float
    valid_nights: int
    confidence: float
    last_sleep_day: str
    window_start_day: str
    window_end_day: str
    algorithm_version: str
    updated_at: str


@dataclass(frozen=True)
class SleepEpisode:
    user_id: str
    agent_id: str
    sleep_day: str
    timezone: str
    predicted_start_at: str
    predicted_wake_at: str
    hard_expire_at: str
    confirmed_end_at: str | None
    state: str
    awake_lease_until: str | None
    deferred_sources: tuple[str, ...]
    wake_followup_pending: bool
    wake_followup_consumed_at: str | None
    wake_followup_sent_at: str | None
    updated_at: str


@dataclass(frozen=True)
class SleepGateDecision:
    blocked: bool
    source: str
    state: str
    sleep_day: str | None = None
    blocked_until: datetime | None = None
    confidence: float | None = None
    wake_followup_due: bool = False
    deferred_sources: tuple[str, ...] = ()

    def audit_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "state": self.state,
            "sleep_day": self.sleep_day,
            "blocked_until": (
                self.blocked_until.isoformat() if self.blocked_until else None
            ),
            "confidence": self.confidence,
            "deferred_sources": list(self.deferred_sources),
            "wake_followup_due": self.wake_followup_due,
        }


class SleepContextStore:
    """Persist slow-changing routines and finite per-wake-day sleep episodes."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(str(self.path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sleep_routine_profiles (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    timezone TEXT NOT NULL,
                    bedtime_center_minute REAL NOT NULL,
                    wake_center_minute REAL NOT NULL,
                    bedtime_spread_minutes REAL NOT NULL,
                    wake_spread_minutes REAL NOT NULL,
                    valid_nights INTEGER NOT NULL,
                    confidence REAL NOT NULL,
                    last_sleep_day TEXT NOT NULL,
                    window_start_day TEXT NOT NULL,
                    window_end_day TEXT NOT NULL,
                    algorithm_version TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, agent_id)
                );

                CREATE TABLE IF NOT EXISTS sleep_episodes (
                    user_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'default',
                    sleep_day TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    predicted_start_at TEXT NOT NULL,
                    predicted_wake_at TEXT NOT NULL,
                    hard_expire_at TEXT NOT NULL,
                    confirmed_end_at TEXT,
                    state TEXT NOT NULL,
                    awake_lease_until TEXT,
                    deferred_sources_json TEXT NOT NULL DEFAULT '[]',
                    wake_followup_pending INTEGER NOT NULL DEFAULT 0,
                    wake_followup_consumed_at TEXT,
                    wake_followup_sent_at TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, agent_id, sleep_day)
                );

                CREATE INDEX IF NOT EXISTS idx_sleep_episodes_pending
                    ON sleep_episodes(user_id, agent_id, wake_followup_pending,
                                      hard_expire_at);
                """
            )

    @staticmethod
    def _profile_from_row(row: sqlite3.Row | None) -> SleepRoutineProfile | None:
        return SleepRoutineProfile(**dict(row)) if row is not None else None

    @staticmethod
    def _episode_from_row(row: sqlite3.Row | None) -> SleepEpisode | None:
        if row is None:
            return None
        data = dict(row)
        data["deferred_sources"] = tuple(
            item
            for item in json.loads(data.pop("deferred_sources_json") or "[]")
            if isinstance(item, str)
        )
        data["wake_followup_pending"] = bool(data["wake_followup_pending"])
        return SleepEpisode(**data)

    def get_profile(
        self, user_id: str, agent_id: str = "default"
    ) -> SleepRoutineProfile | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sleep_routine_profiles "
                "WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            ).fetchone()
        return self._profile_from_row(row)

    def save_profile(self, profile: SleepRoutineProfile) -> None:
        values = asdict(profile)
        columns = tuple(values)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO sleep_routine_profiles ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)}) "
                "ON CONFLICT(user_id, agent_id) DO UPDATE SET "
                + ", ".join(
                    f"{column}=excluded.{column}"
                    for column in columns
                    if column not in {"user_id", "agent_id"}
                ),
                tuple(values[column] for column in columns),
            )

    def get_episode(
        self, user_id: str, agent_id: str, sleep_day: str
    ) -> SleepEpisode | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sleep_episodes WHERE user_id = ? "
                "AND agent_id = ? AND sleep_day = ?",
                (user_id, agent_id, sleep_day),
            ).fetchone()
        return self._episode_from_row(row)

    def save_episode(self, episode: SleepEpisode) -> None:
        values = asdict(episode)
        values["deferred_sources_json"] = json.dumps(
            list(values.pop("deferred_sources")),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        values["wake_followup_pending"] = int(values["wake_followup_pending"])
        columns = tuple(values)
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO sleep_episodes ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)}) "
                "ON CONFLICT(user_id, agent_id, sleep_day) DO UPDATE SET "
                + ", ".join(
                    f"{column}=excluded.{column}"
                    for column in columns
                    if column not in {"user_id", "agent_id", "sleep_day"}
                ),
                tuple(values[column] for column in columns),
            )

    def latest_pending_episode(
        self, user_id: str, agent_id: str
    ) -> SleepEpisode | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM sleep_episodes WHERE user_id = ? AND agent_id = ? "
                "AND wake_followup_pending = 1 AND wake_followup_consumed_at IS NULL "
                "ORDER BY hard_expire_at DESC LIMIT 1",
                (user_id, agent_id),
            ).fetchone()
        return self._episode_from_row(row)

    def defer(self, episode: SleepEpisode, trigger_source: str) -> SleepEpisode:
        sources = tuple(dict.fromkeys((*episode.deferred_sources, trigger_source)))
        updated = SleepEpisode(
            **{
                **asdict(episode),
                "deferred_sources": sources,
                "wake_followup_pending": True,
                "updated_at": _utcnow().isoformat(),
            }
        )
        self.save_episode(updated)
        return updated

    def consume_followup(
        self,
        user_id: str,
        agent_id: str,
        sleep_day: str,
        *,
        sent: bool,
        now: datetime | None = None,
    ) -> None:
        timestamp = _aware(now or _utcnow()).isoformat()
        with self._connect() as connection:
            connection.execute(
                "UPDATE sleep_episodes SET wake_followup_pending = 0, "
                "wake_followup_consumed_at = ?, "
                "wake_followup_sent_at = CASE WHEN ? THEN ? ELSE wake_followup_sent_at END, "
                "updated_at = ? WHERE user_id = ? AND agent_id = ? AND sleep_day = ?",
                (
                    timestamp,
                    int(sent),
                    timestamp,
                    timestamp,
                    user_id,
                    agent_id,
                    sleep_day,
                ),
            )

    def clear_pending_for_activity(
        self,
        user_id: str,
        agent_id: str,
        *,
        now: datetime,
        lease_until: datetime,
        clear_pending: bool = True,
    ) -> None:
        timestamp = _aware(now).isoformat()
        with self._connect() as connection:
            if clear_pending:
                connection.execute(
                    "UPDATE sleep_episodes SET state = 'awake_lease', "
                    "awake_lease_until = ?, wake_followup_pending = 0, "
                    "wake_followup_consumed_at = COALESCE("
                    "wake_followup_consumed_at, ?), updated_at = ? "
                    "WHERE user_id = ? AND agent_id = ? AND predicted_start_at <= ? "
                    "AND hard_expire_at > ?",
                    (
                        _aware(lease_until).isoformat(),
                        timestamp,
                        timestamp,
                        user_id,
                        agent_id,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE sleep_episodes SET state = 'awake_lease', "
                    "awake_lease_until = ?, updated_at = ? "
                    "WHERE user_id = ? AND agent_id = ? AND predicted_start_at <= ? "
                    "AND hard_expire_at > ?",
                    (
                        _aware(lease_until).isoformat(),
                        timestamp,
                        user_id,
                        agent_id,
                        timestamp,
                        timestamp,
                    ),
                )

    def recent_wake_delivery(
        self,
        user_id: str,
        agent_id: str,
        *,
        since: datetime,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sleep_episodes WHERE user_id = ? AND agent_id = ? "
                "AND wake_followup_sent_at >= ? LIMIT 1",
                (user_id, agent_id, _aware(since).isoformat()),
            ).fetchone()
        return row is not None

    def has_due_followup(
        self, user_id: str, agent_id: str, *, now: datetime
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM sleep_episodes WHERE user_id = ? AND agent_id = ? "
                "AND wake_followup_pending = 1 AND wake_followup_consumed_at IS NULL "
                "AND hard_expire_at <= ? LIMIT 1",
                (user_id, agent_id, _aware(now).isoformat()),
            ).fetchone()
        return row is not None

    def clear(self, user_id: str, agent_id: str = "default") -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM sleep_routine_profiles WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )
            connection.execute(
                "DELETE FROM sleep_episodes WHERE user_id = ? AND agent_id = ?",
                (user_id, agent_id),
            )


class SleepContextController:
    """Build personal sleep windows and turn blocked checks into one wake chance."""

    def __init__(
        self,
        *,
        store: SleepContextStore,
        sleep_score_store: Any,
        timezone_resolver: Callable[[str], str],
        lookback_days: int = 28,
        min_nights: int = 5,
        confidence_threshold: float = 0.55,
        wake_tail_minutes: int = 30,
        end_early_tolerance_minutes: int = 30,
        end_stability_minutes: int = 45,
        max_window_hours: int = 14,
        awake_lease_minutes: int = 90,
        wake_delivery_cooldown_minutes: int = 30,
        wake_opportunity_ttl_minutes: int = 360,
    ) -> None:
        self.store = store
        self.sleep_score_store = sleep_score_store
        self.timezone_resolver = timezone_resolver
        self.lookback_days = max(7, lookback_days)
        self.min_nights = max(2, min_nights)
        self.confidence_threshold = max(0.0, min(1.0, confidence_threshold))
        self.wake_tail_minutes = max(0, wake_tail_minutes)
        self.end_early_tolerance_minutes = max(0, end_early_tolerance_minutes)
        self.end_stability_minutes = max(0, end_stability_minutes)
        self.max_window_hours = max(8, max_window_hours)
        self.awake_lease_minutes = max(1, awake_lease_minutes)
        self.wake_delivery_cooldown_minutes = max(1, wake_delivery_cooldown_minutes)
        self.wake_opportunity_ttl_minutes = max(30, wake_opportunity_ttl_minutes)

    def _timezone(self, user_id: str) -> str:
        return self.timezone_resolver(user_id) or "Asia/Shanghai"

    @staticmethod
    def _mad(values: list[float], center: float) -> float:
        return float(median([abs(value - center) for value in values])) if values else 0.0

    def refresh_profile(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
    ) -> SleepRoutineProfile | None:
        tz_name = self._timezone(user_id)
        zone = resolve_zoneinfo(tz_name)
        local_now = _aware(now or _utcnow()).astimezone(zone)
        start_day = local_now.date() - timedelta(days=self.lookback_days - 1)
        scores = self.sleep_score_store.list_scores(
            user_id, start_day.isoformat(), local_now.date().isoformat()
        )
        bed_minutes: list[float] = []
        wake_minutes: list[float] = []
        valid_days: list[str] = []
        for score in scores:
            started = _parse_time(getattr(score, "session_start", None))
            ended = _parse_time(getattr(score, "session_end", None))
            sleep_day = str(getattr(score, "sleep_day", "") or "")
            if started is None or ended is None or not sleep_day:
                continue
            duration = (ended - started).total_seconds() / 60.0
            future_cutoff = _aware(now or _utcnow()) + timedelta(minutes=5)
            if duration < 180 or duration > 14 * 60 or ended > future_cutoff:
                continue
            try:
                wake_day = date.fromisoformat(sleep_day)
            except ValueError:
                continue
            midnight = datetime.combine(wake_day, time.min, tzinfo=zone)
            bed_minutes.append(
                (started.astimezone(zone) - midnight).total_seconds() / 60.0
            )
            wake_minutes.append(
                (ended.astimezone(zone) - midnight).total_seconds() / 60.0
            )
            valid_days.append(sleep_day)

        if len(valid_days) < self.min_nights:
            existing = self.store.get_profile(user_id, agent_id)
            if existing is None:
                return None
            try:
                age_days = max(
                    0,
                    (local_now.date() - date.fromisoformat(existing.last_sleep_day)).days,
                )
            except ValueError:
                age_days = self.lookback_days
            freshness_cap = max(0.0, 1.0 - max(0, age_days - 2) / 14.0)
            decayed_confidence = round(
                min(existing.confidence, freshness_cap),
                3,
            )
            if (
                decayed_confidence != existing.confidence
                or existing.timezone != tz_name
            ):
                existing = SleepRoutineProfile(
                    **{
                        **asdict(existing),
                        "timezone": tz_name,
                        "confidence": decayed_confidence,
                        "updated_at": _aware(now or _utcnow()).isoformat(),
                    }
                )
                self.store.save_profile(existing)
            return existing

        bedtime_center = float(median(bed_minutes))
        wake_center = float(median(wake_minutes))
        bedtime_spread = self._mad(bed_minutes, bedtime_center)
        wake_spread = self._mad(wake_minutes, wake_center)
        last_day = max(valid_days)
        age_days = max(0, (local_now.date() - date.fromisoformat(last_day)).days)
        sample_span = max(1, 14 - self.min_nights)
        sample_progress = min(
            1.0,
            max(0.0, (len(valid_days) - self.min_nights) / sample_span),
        )
        # Meeting min_nights makes a stable profile usable. More nights then
        # strengthen its confidence gradually instead of acting as a hard jump.
        count_factor = 0.6 + (0.4 * sample_progress)
        stability = max(0.0, 1.0 - max(bedtime_spread, wake_spread) / 240.0)
        freshness = max(0.0, 1.0 - max(0, age_days - 2) / 14.0)
        confidence = round(count_factor * (0.5 + 0.5 * stability) * freshness, 3)
        profile = SleepRoutineProfile(
            user_id=user_id,
            agent_id=agent_id,
            timezone=tz_name,
            bedtime_center_minute=round(bedtime_center, 2),
            wake_center_minute=round(wake_center, 2),
            bedtime_spread_minutes=round(bedtime_spread, 2),
            wake_spread_minutes=round(wake_spread, 2),
            valid_nights=len(valid_days),
            confidence=confidence,
            last_sleep_day=last_day,
            window_start_day=min(valid_days),
            window_end_day=max(valid_days),
            algorithm_version=PROFILE_ALGORITHM_VERSION,
            updated_at=_aware(now or _utcnow()).isoformat(),
        )
        self.store.save_profile(profile)
        return profile

    def _bounds(
        self, profile: SleepRoutineProfile, sleep_day: date
    ) -> tuple[datetime, datetime, datetime]:
        zone = resolve_zoneinfo(profile.timezone)
        midnight = datetime.combine(sleep_day, time.min, tzinfo=zone)
        start_margin = min(60.0, max(15.0, profile.bedtime_spread_minutes / 2.0))
        predicted_start = midnight + timedelta(
            minutes=profile.bedtime_center_minute - start_margin
        )
        predicted_wake = midnight + timedelta(minutes=profile.wake_center_minute)
        # A fixed tail is too short for users whose wake time naturally varies.
        # Keep the extra allowance finite so a missing health sync can never
        # leave the episode permanently blocked.
        adaptive_tail = self.wake_tail_minutes + min(
            90.0, max(0.0, profile.wake_spread_minutes)
        )
        hard_expire = predicted_wake + timedelta(minutes=adaptive_tail)
        max_expire = predicted_start + timedelta(hours=self.max_window_hours)
        if hard_expire > max_expire:
            hard_expire = max_expire
        return predicted_start, predicted_wake, hard_expire

    def _candidate(
        self, profile: SleepRoutineProfile, local_now: datetime
    ) -> tuple[date, datetime, datetime, datetime] | None:
        for sleep_day in (local_now.date(), local_now.date() + timedelta(days=1)):
            start, wake, expire = self._bounds(profile, sleep_day)
            if start <= local_now <= expire:
                return sleep_day, start, wake, expire
        return None

    def _ensure_episode(
        self,
        profile: SleepRoutineProfile,
        sleep_day: date,
        start: datetime,
        wake: datetime,
        expire: datetime,
    ) -> SleepEpisode:
        existing = self.store.get_episode(
            profile.user_id, profile.agent_id, sleep_day.isoformat()
        )
        if existing is not None:
            return existing
        now_text = _utcnow().isoformat()
        episode = SleepEpisode(
            user_id=profile.user_id,
            agent_id=profile.agent_id,
            sleep_day=sleep_day.isoformat(),
            timezone=profile.timezone,
            predicted_start_at=start.astimezone(timezone.utc).isoformat(),
            predicted_wake_at=wake.astimezone(timezone.utc).isoformat(),
            hard_expire_at=expire.astimezone(timezone.utc).isoformat(),
            confirmed_end_at=None,
            state="predicted_sleeping",
            awake_lease_until=None,
            deferred_sources=(),
            wake_followup_pending=False,
            wake_followup_consumed_at=None,
            wake_followup_sent_at=None,
            updated_at=now_text,
        )
        self.store.save_episode(episode)
        return episode

    def _confirmed_end(
        self,
        user_id: str,
        sleep_day: str,
        *,
        now: datetime,
        predicted_wake_at: str | None,
    ) -> datetime | None:
        scores = self.sleep_score_store.list_scores(user_id, sleep_day, sleep_day)
        if not scores:
            return None
        score = scores[-1]
        ended = _parse_time(getattr(score, "session_end", None))
        if ended is None or ended > _aware(now):
            return None

        # The current sleep score is rebuilt while the provider is still
        # delivering an in-progress night. Its session_end is a rolling tail,
        # not proof that the user woke. Reject implausibly early tails and
        # require the score input to remain unchanged for a bounded period.
        predicted_wake = _parse_time(predicted_wake_at)
        if predicted_wake is None:
            return None
        earliest_end = predicted_wake - timedelta(
            minutes=self.end_early_tolerance_minutes
        )
        if ended < earliest_end:
            return None

        meta = getattr(score, "meta", None)
        computed_at = getattr(meta, "computed_at", None)
        if not isinstance(computed_at, (int, float)):
            return None
        divisor = 1000.0 if computed_at > 10_000_000_000 else 1.0
        computed = datetime.fromtimestamp(computed_at / divisor, tz=timezone.utc)
        now_utc = _aware(now)
        if computed > now_utc:
            return None
        if now_utc - computed < timedelta(minutes=self.end_stability_minutes):
            return None
        return ended

    def evaluate(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
        live_stage: bool | None = None,
    ) -> SleepGateDecision:
        now_utc = _aware(now or _utcnow())
        profile = self.refresh_profile(user_id, agent_id, now=now_utc)

        pending = self.store.latest_pending_episode(user_id, agent_id)
        if pending is not None:
            if live_stage is True:
                return SleepGateDecision(
                    blocked=True,
                    source="fresh_sleep_stage",
                    state="confirmed_sleeping",
                    sleep_day=pending.sleep_day,
                    confidence=1.0,
                    deferred_sources=pending.deferred_sources,
                )
            confirmed_end = self._confirmed_end(
                user_id,
                pending.sleep_day,
                now=now_utc,
                predicted_wake_at=pending.predicted_wake_at,
            )
            expire = _parse_time(pending.hard_expire_at)
            release_source: str | None = None
            release_at: datetime | None = None
            if confirmed_end is not None:
                release_source = "confirmed_sleep_end"
                release_at = confirmed_end
            elif live_stage is False:
                release_source = "fresh_awake_stage"
                release_at = now_utc
            elif expire is not None and now_utc >= expire:
                release_source = "personal_window_expired"
                release_at = expire

            if release_source is not None:
                if (
                    release_at is not None
                    and live_stage is not False
                    and now_utc - release_at
                    > timedelta(minutes=self.wake_opportunity_ttl_minutes)
                ):
                    self.store.consume_followup(
                        user_id,
                        agent_id,
                        pending.sleep_day,
                        sent=False,
                        now=now_utc,
                    )
                    return SleepGateDecision(
                        blocked=False,
                        source="stale_wake_opportunity",
                        state="awake",
                        sleep_day=pending.sleep_day,
                        confidence=profile.confidence if profile else None,
                    )
                state = (
                    "awake"
                    if release_source in {"confirmed_sleep_end", "fresh_awake_stage"}
                    else "expired"
                )
                updated = SleepEpisode(
                    **{
                        **asdict(pending),
                        "confirmed_end_at": (
                            confirmed_end.isoformat()
                            if confirmed_end is not None
                            else pending.confirmed_end_at
                        ),
                        "state": state,
                        "awake_lease_until": (
                            (
                                now_utc
                                + timedelta(minutes=self.awake_lease_minutes)
                            ).isoformat()
                            if live_stage is False
                            else pending.awake_lease_until
                        ),
                        "updated_at": now_utc.isoformat(),
                    }
                )
                self.store.save_episode(updated)
                return SleepGateDecision(
                    blocked=False,
                    source=release_source,
                    state=state,
                    sleep_day=updated.sleep_day,
                    confidence=profile.confidence if profile else None,
                    wake_followup_due=True,
                    deferred_sources=updated.deferred_sources,
                )

        if live_stage is True and (
            profile is None or profile.confidence < self.confidence_threshold
        ):
            return SleepGateDecision(
                blocked=True,
                source="fresh_sleep_stage",
                state="confirmed_sleeping",
                confidence=1.0,
            )
        if live_stage is False and (
            profile is None or profile.confidence < self.confidence_threshold
        ):
            return SleepGateDecision(
                blocked=False,
                source="fresh_awake_stage",
                state="awake_lease",
                confidence=1.0,
            )

        if profile is None or profile.confidence < self.confidence_threshold:
            return SleepGateDecision(
                blocked=False,
                source="fixed_window_fallback",
                state="unknown",
                confidence=profile.confidence if profile else None,
            )

        zone = resolve_zoneinfo(profile.timezone)
        local_now = now_utc.astimezone(zone)
        candidate = self._candidate(profile, local_now)
        if candidate is None:
            return SleepGateDecision(
                blocked=False,
                source="personal_window",
                state="awake",
                confidence=profile.confidence,
            )
        sleep_day, start, wake, expire = candidate
        episode = self._ensure_episode(profile, sleep_day, start, wake, expire)

        if live_stage is True:
            return SleepGateDecision(
                blocked=True,
                source="fresh_sleep_stage",
                state="confirmed_sleeping",
                sleep_day=episode.sleep_day,
                blocked_until=_parse_time(episode.hard_expire_at),
                confidence=1.0,
                deferred_sources=episode.deferred_sources,
            )

        if live_stage is False:
            lease_until = now_utc + timedelta(minutes=min(60, self.awake_lease_minutes))
            self.store.clear_pending_for_activity(
                user_id,
                agent_id,
                now=now_utc,
                lease_until=lease_until,
                clear_pending=False,
            )
            return SleepGateDecision(
                blocked=False,
                source="fresh_awake_stage",
                state="awake_lease",
                sleep_day=episode.sleep_day,
                confidence=1.0,
            )

        lease_until = _parse_time(episode.awake_lease_until)
        if lease_until is not None and now_utc < lease_until:
            return SleepGateDecision(
                blocked=False,
                source="user_activity_lease",
                state="awake_lease",
                sleep_day=episode.sleep_day,
                confidence=profile.confidence,
            )

        confirmed_end = self._confirmed_end(
            user_id,
            episode.sleep_day,
            now=now_utc,
            predicted_wake_at=episode.predicted_wake_at,
        )
        if confirmed_end is not None:
            updated = SleepEpisode(
                **{
                    **asdict(episode),
                    "confirmed_end_at": confirmed_end.isoformat(),
                    "state": "awake",
                    "updated_at": now_utc.isoformat(),
                }
            )
            self.store.save_episode(updated)
            return SleepGateDecision(
                blocked=False,
                source="confirmed_sleep_end",
                state="awake",
                sleep_day=episode.sleep_day,
                confidence=profile.confidence,
                wake_followup_due=(
                    episode.wake_followup_pending
                    and episode.wake_followup_consumed_at is None
                ),
                deferred_sources=episode.deferred_sources,
            )

        hard_expire = _parse_time(episode.hard_expire_at)
        if hard_expire is not None and now_utc < hard_expire:
            return SleepGateDecision(
                blocked=True,
                source="personal_sleep_window",
                state="predicted_sleeping",
                sleep_day=episode.sleep_day,
                blocked_until=hard_expire,
                confidence=profile.confidence,
                deferred_sources=episode.deferred_sources,
            )

        return SleepGateDecision(
            blocked=False,
            source="personal_window_expired",
            state="expired",
            sleep_day=episode.sleep_day,
            confidence=profile.confidence,
            wake_followup_due=(
                episode.wake_followup_pending
                and episode.wake_followup_consumed_at is None
            ),
            deferred_sources=episode.deferred_sources,
        )

    def defer(
        self,
        user_id: str,
        agent_id: str,
        trigger_source: str,
        gate: SleepGateDecision,
    ) -> None:
        if not gate.sleep_day:
            return
        episode = self.store.get_episode(user_id, agent_id, gate.sleep_day)
        if episode is not None:
            self.store.defer(episode, trigger_source)

    def consume(
        self,
        user_id: str,
        agent_id: str,
        gate: SleepGateDecision,
        *,
        sent: bool,
        now: datetime | None = None,
    ) -> None:
        if not gate.wake_followup_due or not gate.sleep_day:
            return
        self.store.consume_followup(
            user_id, agent_id, gate.sleep_day, sent=sent, now=now
        )

    def record_user_activity(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
    ) -> None:
        now_utc = _aware(now or _utcnow())
        profile = self.refresh_profile(user_id, agent_id, now=now_utc)
        if profile is None or profile.confidence < self.confidence_threshold:
            return
        local_now = now_utc.astimezone(resolve_zoneinfo(profile.timezone))
        candidate = self._candidate(profile, local_now)
        if candidate is None:
            return
        sleep_day, start, wake, expire = candidate
        self._ensure_episode(profile, sleep_day, start, wake, expire)
        self.store.clear_pending_for_activity(
            user_id,
            agent_id,
            now=now_utc,
            lease_until=now_utc + timedelta(minutes=self.awake_lease_minutes),
        )

    def has_due_followup(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
    ) -> bool:
        return self.store.has_due_followup(
            user_id, agent_id, now=_aware(now or _utcnow())
        )

    def recent_wake_delivery(
        self,
        user_id: str,
        agent_id: str = "default",
        *,
        now: datetime | None = None,
    ) -> bool:
        return self.store.recent_wake_delivery(
            user_id,
            agent_id,
            since=_aware(now or _utcnow())
            - timedelta(minutes=self.wake_delivery_cooldown_minutes),
        )

    def wake_context_text(
        self, user_id: str, agent_id: str, gate: SleepGateDecision
    ) -> str:
        sources = ", ".join(gate.deferred_sources) or "unknown"
        return (
            f"Sleep episode {gate.sleep_day or 'unknown'} has ended or expired. "
            f"Previously suppressed trigger sources: {sources}. Re-read current data; "
            "do not replay old wording. Send at most one useful, low-pressure message, "
            "and do not claim the user just woke unless confirmed sleep-end evidence exists."
        )

    def clear(self, user_id: str, agent_id: str = "default") -> None:
        self.store.clear(user_id, agent_id)
