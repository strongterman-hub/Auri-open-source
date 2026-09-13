from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.proactive.sleep_context import SleepContextController, SleepContextStore


ZONE = ZoneInfo("Asia/Shanghai")


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _score(
    sleep_day: date,
    start: str,
    end: str,
    *,
    computed_at: datetime | None = None,
):
    midnight = datetime.combine(sleep_day, time.min, tzinfo=ZONE)
    start_hour, start_minute = (int(item) for item in start.split(":"))
    end_hour, end_minute = (int(item) for item in end.split(":"))
    started = midnight + timedelta(hours=start_hour, minutes=start_minute)
    ended = midnight + timedelta(hours=end_hour, minutes=end_minute)
    if start_hour >= 18:
        started -= timedelta(days=1)
    return SimpleNamespace(
        sleep_day=sleep_day.isoformat(),
        session_start=_utc_iso(started),
        session_end=_utc_iso(ended),
        meta=SimpleNamespace(
            computed_at=int((computed_at or ended).timestamp() * 1000)
        ),
    )


class FakeSleepScoreStore:
    def __init__(self, scores: list[SimpleNamespace]) -> None:
        self.scores = list(scores)

    def list_scores(self, _user_id: str, from_day: str, to_day: str):
        return [
            score
            for score in self.scores
            if from_day <= score.sleep_day <= to_day
        ]


def _controller(tmp_path, scores, **kwargs) -> SleepContextController:
    options = {
        "min_nights": 5,
        "confidence_threshold": 0.55,
    }
    options.update(kwargs)
    return SleepContextController(
        store=SleepContextStore(tmp_path / "sleep_context.db"),
        sleep_score_store=FakeSleepScoreStore(scores),
        timezone_resolver=lambda _user_id: "Asia/Shanghai",
        **options,
    )


def _stable_history(last_day: date) -> list[SimpleNamespace]:
    return [
        _score(last_day - timedelta(days=offset), "02:55", "11:15")
        for offset in range(7, 0, -1)
    ]


def test_personal_window_blocks_late_sleeper_without_live_stage(tmp_path) -> None:
    controller = _controller(tmp_path, _stable_history(date(2026, 9, 5)))
    now = datetime(2026, 9, 5, 8, 5, tzinfo=ZONE)

    gate = controller.evaluate("u1", now=now)

    assert gate.blocked is True
    assert gate.source == "personal_sleep_window"
    assert gate.sleep_day == "2026-09-05"
    assert gate.blocked_until == datetime(2026, 9, 5, 11, 45, tzinfo=ZONE)
    profile = controller.store.get_profile("u1")
    assert profile is not None
    assert profile.bedtime_center_minute == 175
    assert profile.wake_center_minute == 675
    assert profile.valid_nights == 7
    assert profile.confidence >= 0.55


def test_confirmed_sleep_end_releases_one_deferred_wake_followup(tmp_path) -> None:
    history = _stable_history(date(2026, 9, 5))
    controller = _controller(tmp_path, history)
    sleeping_now = datetime(2026, 9, 5, 8, 5, tzinfo=ZONE)
    gate = controller.evaluate("u1", now=sleeping_now)
    controller.defer("u1", "default", "health", gate)
    controller.defer("u1", "default", "weather", gate)

    controller.sleep_score_store.scores.append(
        _score(date(2026, 9, 5), "03:56", "11:05")
    )
    wake_check = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 11, 57, tzinfo=ZONE)
    )

    assert wake_check.blocked is False
    assert wake_check.source == "confirmed_sleep_end"
    assert wake_check.wake_followup_due is True
    assert wake_check.deferred_sources == ("health", "weather")

    controller.consume("u1", "default", wake_check, sent=True)
    repeated = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 11, 58, tzinfo=ZONE)
    )
    assert repeated.wake_followup_due is False
    assert controller.recent_wake_delivery(
        "u1", now=datetime(2026, 9, 5, 11, 58, tzinfo=ZONE)
    ) is True


def test_rolling_sleep_tail_does_not_release_before_expected_wake(tmp_path) -> None:
    history = _stable_history(date(2026, 9, 5))
    controller = _controller(tmp_path, history)
    gate = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE)
    )
    controller.defer("u1", "default", "health", gate)

    # A provider sync can expose a score whose tail is already in the past
    # even though the user is still asleep. Age alone must not make it final.
    controller.sleep_score_store.scores.append(
        _score(
            date(2026, 9, 5),
            "03:56",
            "08:45",
            computed_at=datetime(2026, 9, 5, 8, 54, tzinfo=ZONE),
        )
    )
    still_blocked = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 9, 35, tzinfo=ZONE)
    )

    assert still_blocked.blocked is True
    assert still_blocked.source == "personal_sleep_window"
    assert still_blocked.wake_followup_due is False


def test_recent_sleep_end_waits_for_stability_before_release(tmp_path) -> None:
    history = _stable_history(date(2026, 9, 5))
    controller = _controller(tmp_path, history)
    gate = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE)
    )
    controller.defer("u1", "default", "health", gate)
    controller.sleep_score_store.scores.append(
        _score(
            date(2026, 9, 5),
            "03:56",
            "11:05",
            computed_at=datetime(2026, 9, 5, 11, 6, tzinfo=ZONE),
        )
    )

    unstable = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 11, 20, tzinfo=ZONE)
    )
    stable = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 11, 51, tzinfo=ZONE)
    )

    assert unstable.blocked is True
    assert unstable.source == "personal_sleep_window"
    assert stable.blocked is False
    assert stable.source == "confirmed_sleep_end"
    assert stable.wake_followup_due is True


def test_wake_spread_extends_but_bounds_hard_expiry(tmp_path) -> None:
    sleep_day = date(2026, 9, 5)
    history = [
        _score(sleep_day - timedelta(days=offset), "03:00", wake)
        for offset, wake in enumerate(
            ["10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00"],
            start=1,
        )
    ]
    controller = _controller(tmp_path, history)

    gate = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 9, 0, tzinfo=ZONE)
    )

    assert gate.blocked is True
    assert gate.blocked_until == datetime(2026, 9, 5, 13, 0, tzinfo=ZONE)
    controller.defer("u1", "default", "time", gate)
    expired = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 13, 1, tzinfo=ZONE)
    )
    assert expired.blocked is False
    assert expired.source == "personal_window_expired"


def test_hard_expiry_releases_without_sleep_end_or_user_reply(tmp_path) -> None:
    controller = _controller(tmp_path, _stable_history(date(2026, 9, 5)))
    gate = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE)
    )
    controller.defer("u1", "default", "time", gate)

    assert controller.has_due_followup(
        "u1", now=datetime(2026, 9, 5, 11, 46, tzinfo=ZONE)
    ) is True
    expired = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 11, 46, tzinfo=ZONE)
    )
    assert expired.blocked is False
    assert expired.source == "personal_window_expired"
    assert expired.wake_followup_due is True


def test_user_activity_lease_clears_deferred_followup(tmp_path) -> None:
    controller = _controller(tmp_path, _stable_history(date(2026, 9, 5)))
    gate = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE)
    )
    controller.defer("u1", "default", "health", gate)

    controller.record_user_activity(
        "u1", now=datetime(2026, 9, 5, 10, 31, tzinfo=ZONE)
    )
    during_lease = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 10, 46, tzinfo=ZONE)
    )

    assert during_lease.blocked is False
    assert during_lease.source == "user_activity_lease"
    assert during_lease.wake_followup_due is False
    episode = controller.store.get_episode("u1", "default", "2026-09-05")
    assert episode is not None
    assert episode.wake_followup_pending is False
    assert episode.wake_followup_consumed_at is not None


def test_low_confidence_profile_requests_fixed_window_fallback(tmp_path) -> None:
    controller = _controller(
        tmp_path,
        _stable_history(date(2026, 9, 5)),
        confidence_threshold=0.99,
    )

    gate = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE)
    )

    assert gate.blocked is False
    assert gate.source == "fixed_window_fallback"


def test_live_sleep_stage_remains_authoritative_without_profile(tmp_path) -> None:
    controller = _controller(tmp_path, [])

    asleep = controller.evaluate(
        "u1",
        now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE),
        live_stage=True,
    )
    awake = controller.evaluate(
        "u1",
        now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE),
        live_stage=False,
    )

    assert asleep.blocked is True
    assert asleep.source == "fresh_sleep_stage"
    assert awake.blocked is False
    assert awake.source == "fresh_awake_stage"


def test_fresh_awake_stage_releases_pending_opportunity_early(tmp_path) -> None:
    controller = _controller(tmp_path, _stable_history(date(2026, 9, 5)))
    gate = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE)
    )
    controller.defer("u1", "default", "health", gate)

    awake = controller.evaluate(
        "u1",
        now=datetime(2026, 9, 5, 9, 0, tzinfo=ZONE),
        live_stage=False,
    )

    assert awake.blocked is False
    assert awake.source == "fresh_awake_stage"
    assert awake.wake_followup_due is True
    assert awake.deferred_sources == ("health",)


def test_fresh_sleep_stage_wins_over_expired_personal_window(tmp_path) -> None:
    controller = _controller(tmp_path, _stable_history(date(2026, 9, 5)))
    gate = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE)
    )
    controller.defer("u1", "default", "health", gate)

    still_asleep = controller.evaluate(
        "u1",
        now=datetime(2026, 9, 5, 11, 46, tzinfo=ZONE),
        live_stage=True,
    )

    assert still_asleep.blocked is True
    assert still_asleep.source == "fresh_sleep_stage"


def test_old_wake_opportunity_is_consumed_without_late_delivery(tmp_path) -> None:
    controller = _controller(
        tmp_path,
        _stable_history(date(2026, 9, 5)),
        wake_opportunity_ttl_minutes=360,
    )
    gate = controller.evaluate(
        "u1", now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE)
    )
    controller.defer("u1", "default", "health", gate)

    stale = controller.evaluate(
        "u1", now=datetime(2026, 9, 6, 8, 5, tzinfo=ZONE)
    )

    assert stale.blocked is False
    assert stale.source == "stale_wake_opportunity"
    assert stale.wake_followup_due is False
    episode = controller.store.get_episode("u1", "default", "2026-09-05")
    assert episode is not None
    assert episode.wake_followup_pending is False
    assert episode.wake_followup_consumed_at is not None


def test_stale_profile_decays_back_to_fixed_window(tmp_path) -> None:
    controller = _controller(tmp_path, _stable_history(date(2026, 9, 5)))
    controller.evaluate("u1", now=datetime(2026, 9, 5, 15, 0, tzinfo=ZONE))

    stale = controller.evaluate(
        "u1", now=datetime(2026, 10, 5, 8, 5, tzinfo=ZONE)
    )

    assert stale.source == "fixed_window_fallback"
    assert stale.confidence == 0.0


def test_clear_removes_profile_and_episode(tmp_path) -> None:
    controller = _controller(tmp_path, _stable_history(date(2026, 9, 5)))
    controller.evaluate("u1", now=datetime(2026, 9, 5, 8, 5, tzinfo=ZONE))

    controller.clear("u1")

    assert controller.store.get_profile("u1") is None
    assert controller.store.get_episode("u1", "default", "2026-09-05") is None
