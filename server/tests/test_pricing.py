from __future__ import annotations

from datetime import datetime, timezone

from app.core.pricing import is_peak, normalize_model, pricing_for_time, usage_cost


def _iso(hour: int) -> str:
    return datetime(2026, 8, 21, hour, 0, 0, tzinfo=timezone.utc).isoformat()


def test_normalize_model() -> None:
    assert normalize_model("deepseek-flash") == "deepseek-v4-flash"
    assert normalize_model("deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize_model("deepseek/deepseek-v4-pro") == "deepseek-v4-pro"
    assert normalize_model("deepseek-v4-flash-vision-exp") == "deepseek-v4-flash-vision-exp"
    assert normalize_model(None) == "deepseek-v4-flash"


def test_is_peak_beijing_hours() -> None:
    # 10:00 Beijing == 02:00 UTC (peak), 20:00 Beijing == 12:00 UTC (off-peak).
    assert is_peak(datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc)) is True
    assert is_peak(datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)) is False
    # 10:00 Beijing on Saturday is off-peak under the current official rule.
    assert is_peak(datetime(2026, 9, 12, 2, 0, tzinfo=timezone.utc)) is False


def test_usage_cost_flash_before_new_price_keeps_historical_amount() -> None:
    offpeak_ts = _iso(12)  # 20:00 Beijing -> off-peak
    peak_ts = _iso(2)  # 10:00 Beijing -> peak

    # 1M cached + 1M uncached + 1M output at off-peak:
    # 1 * 0.05 + 1 * 1.5 + 1 * 4.5 = 6.05
    assert usage_cost("deepseek-v4-flash", offpeak_ts, 1_000_000, 1_000_000, 1_000_000) == 6.05
    # peak: 1 * 0.10 + 1 * 3.0 + 1 * 9.0 = 12.10
    assert usage_cost("deepseek-v4-flash", peak_ts, 1_000_000, 1_000_000, 1_000_000) == 12.10


def test_usage_cost_flash_after_new_price() -> None:
    offpeak_ts = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc).isoformat()
    peak_ts = datetime(2026, 9, 11, 2, 0, tzinfo=timezone.utc).isoformat()

    assert usage_cost("deepseek-flash", offpeak_ts, 1_000_000, 1_000_000, 1_000_000) == 5.02
    assert usage_cost("deepseek-v4-flash", peak_ts, 1_000_000, 1_000_000, 1_000_000) == 10.04
    assert usage_cost("deepseek-v4-flash-vision-exp", peak_ts, 1_000_000, 1_000_000, 1_000_000) == 10.04


def test_flash_price_switches_at_official_effective_time() -> None:
    before = datetime(2026, 9, 10, 3, 59, 59, tzinfo=timezone.utc).isoformat()
    effective = datetime(2026, 9, 10, 4, 0, tzinfo=timezone.utc).isoformat()

    assert usage_cost("deepseek-v4-flash", before, 0, 1_000_000, 1_000_000) == 12.0
    assert usage_cost("deepseek-v4-flash", effective, 0, 1_000_000, 1_000_000) == 5.0


def test_usage_cost_flash_weekend_uses_offpeak_price() -> None:
    saturday_peak_clock = datetime(2026, 9, 12, 2, 0, tzinfo=timezone.utc).isoformat()
    assert usage_cost("deepseek-flash", saturday_peak_clock, 0, 1_000_000, 1_000_000) == 5.0


def test_usage_cost_pro_before_and_after_flash_routing() -> None:
    ts = _iso(12)  # off-peak
    # 1M miss + 1M output = 4.5 + 13.5 = 18.0
    assert usage_cost("deepseek-v4-pro", ts, 0, 1_000_000, 1_000_000) == 18.0

    before_route = datetime(2026, 9, 14, 3, 59, 59, tzinfo=timezone.utc).isoformat()
    after_route = datetime(2026, 9, 14, 4, 0, tzinfo=timezone.utc).isoformat()
    assert usage_cost("deepseek-v4-pro", before_route, 0, 1_000_000, 1_000_000) == 36.0
    assert usage_cost("deepseek-v4-pro", after_route, 0, 1_000_000, 1_000_000) == 5.0


def test_pricing_snapshot_switches_pro_to_flash_at_route_time() -> None:
    before = pricing_for_time(datetime(2026, 9, 14, 3, 59, 59, tzinfo=timezone.utc))
    after = pricing_for_time(datetime(2026, 9, 14, 4, 0, tzinfo=timezone.utc))

    assert before["deepseek-v4-pro"]["output"]["peak"] == 27.0
    assert after["deepseek-v4-pro"] == after["deepseek-v4-flash"]
