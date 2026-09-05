from __future__ import annotations

from datetime import datetime, timezone

from app.core.pricing import is_peak, normalize_model, usage_cost


def _iso(hour: int) -> str:
    return datetime(2026, 8, 21, hour, 0, 0, tzinfo=timezone.utc).isoformat()


def test_normalize_model() -> None:
    assert normalize_model("deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize_model("deepseek/deepseek-v4-pro") == "deepseek-v4-pro"
    assert normalize_model("deepseek-v4-flash-vision-exp") == "deepseek-v4-flash-vision-exp"
    assert normalize_model(None) == "deepseek-v4-flash"


def test_is_peak_beijing_hours() -> None:
    # 10:00 Beijing == 02:00 UTC (peak), 20:00 Beijing == 12:00 UTC (off-peak).
    assert is_peak(datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc)) is True
    assert is_peak(datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)) is False


def test_usage_cost_flash_offpeak_and_peak() -> None:
    offpeak_ts = _iso(12)  # 20:00 Beijing -> off-peak
    peak_ts = _iso(2)  # 10:00 Beijing -> peak

    # 1M cached + 1M uncached + 1M output at off-peak:
    # 1 * 0.05 + 1 * 1.5 + 1 * 4.5 = 6.05
    assert usage_cost("deepseek-v4-flash", offpeak_ts, 1_000_000, 1_000_000, 1_000_000) == 6.05
    # peak: 1 * 0.10 + 1 * 3.0 + 1 * 9.0 = 12.10
    assert usage_cost("deepseek-v4-flash", peak_ts, 1_000_000, 1_000_000, 1_000_000) == 12.10


def test_usage_cost_pro() -> None:
    ts = _iso(12)  # off-peak
    # 1M miss + 1M output = 4.5 + 13.5 = 18.0
    assert usage_cost("deepseek-v4-pro", ts, 0, 1_000_000, 1_000_000) == 18.0
