from __future__ import annotations

from datetime import datetime, timedelta, timezone


# 官方价格（元 / 百万 tokens），来源：
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing
# 空闲时段价格为高峰时段的一半；高峰时段为北京时间 9:00-12:00、14:00-18:00。
PRICING: dict[str, dict[str, dict[str, float]]] = {
    "deepseek-v4-flash": {
        "cache_hit": {"offpeak": 0.05, "peak": 0.10},
        "cache_miss": {"offpeak": 1.5, "peak": 3.0},
        "output": {"offpeak": 4.5, "peak": 9.0},
    },
    "deepseek-v4-pro": {
        "cache_hit": {"offpeak": 0.15, "peak": 0.30},
        "cache_miss": {"offpeak": 4.5, "peak": 9.0},
        "output": {"offpeak": 13.5, "peak": 27.0},
    },
    "deepseek-v4-flash-vision-exp": {
        "cache_hit": {"offpeak": 0.05, "peak": 0.10},
        "cache_miss": {"offpeak": 1.5, "peak": 3.0},
        "output": {"offpeak": 4.5, "peak": 9.0},
    },
}

CN_TZ = timezone(timedelta(hours=8))


def normalize_model(model: str | None) -> str:
    """Map arbitrary provider model strings onto a priced DeepSeek model."""
    name = (model or "").lower()
    if "vision" in name:
        return "deepseek-v4-flash-vision-exp"
    if "pro" in name:
        return "deepseek-v4-pro"
    return "deepseek-v4-flash"


def is_peak(dt: datetime) -> bool:
    local = dt.astimezone(CN_TZ)
    hour = local.hour
    return (9 <= hour < 12) or (14 <= hour < 18)


def resolve_rate(model: str, bucket: str, dt: datetime) -> float:
    rates = PRICING.get(model) or PRICING["deepseek-v4-flash"]
    slot = "peak" if is_peak(dt) else "offpeak"
    return rates[bucket][slot]


def usage_cost(
    model: str,
    ts: str,
    cached_tokens: int,
    uncached_tokens: int,
    completion_tokens: int,
) -> float:
    """Cost in CNY for one usage record."""
    try:
        dt = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)
    model = normalize_model(model)
    hit_rate = resolve_rate(model, "cache_hit", dt)
    miss_rate = resolve_rate(model, "cache_miss", dt)
    output_rate = resolve_rate(model, "output", dt)
    cost = (
        cached_tokens * hit_rate
        + uncached_tokens * miss_rate
        + completion_tokens * output_rate
    ) / 1_000_000
    return round(cost, 8)
