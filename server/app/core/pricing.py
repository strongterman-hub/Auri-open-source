from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Final


# 官方价格（元 / 百万 tokens），来源：
# https://api-docs.deepseek.com/zh-cn/news/news260910
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing
# V4.1 Flash 新价于北京时间 2026-09-10 12:00 生效。高峰时段为工作日
# 9:00-12:00、14:00-18:00，空闲时段为高峰半价。
PRICING: dict[str, dict[str, dict[str, float]]] = {
    "deepseek-v4-flash": {
        "cache_hit": {"offpeak": 0.02, "peak": 0.04},
        "cache_miss": {"offpeak": 1.0, "peak": 2.0},
        "output": {"offpeak": 4.0, "peak": 8.0},
    },
    "deepseek-v4-pro": {
        "cache_hit": {"offpeak": 0.15, "peak": 0.30},
        "cache_miss": {"offpeak": 4.5, "peak": 9.0},
        "output": {"offpeak": 13.5, "peak": 27.0},
    },
    "deepseek-v4-flash-vision-exp": {
        "cache_hit": {"offpeak": 0.02, "peak": 0.04},
        "cache_miss": {"offpeak": 1.0, "peak": 2.0},
        "output": {"offpeak": 4.0, "peak": 8.0},
    },
}

# 保留生效前的 Auri 记账口径，避免后台重算历史日志时改写历史费用展示。
LEGACY_PRICING: dict[str, dict[str, dict[str, float]]] = {
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
FLASH_PRICE_EFFECTIVE_AT: Final = datetime(2026, 9, 10, 12, 0, tzinfo=CN_TZ)
PRO_FLASH_ROUTE_AT: Final = datetime(2026, 9, 14, 12, 0, tzinfo=CN_TZ)


def normalize_model(model: str | None) -> str:
    """Map arbitrary provider model strings onto a priced DeepSeek model."""
    name = (model or "").lower()
    if "vision" in name:
        return "deepseek-v4-flash-vision-exp"
    if "pro" in name:
        return "deepseek-v4-pro"
    return "deepseek-v4-flash"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def is_peak(dt: datetime) -> bool:
    local = _aware(dt).astimezone(CN_TZ)
    hour = local.hour
    return local.weekday() < 5 and ((9 <= hour < 12) or (14 <= hour < 18))


def _is_legacy_peak(dt: datetime) -> bool:
    """Preserve the pre-change Auri calculation for historical usage records."""
    hour = _aware(dt).astimezone(CN_TZ).hour
    return (9 <= hour < 12) or (14 <= hour < 18)


def pricing_for_time(dt: datetime) -> dict[str, dict[str, dict[str, float]]]:
    """Return the rate table that applies at ``dt`` without mutating constants."""
    local = _aware(dt).astimezone(CN_TZ)
    source = LEGACY_PRICING if local < FLASH_PRICE_EFFECTIVE_AT else PRICING
    table = {
        model: {bucket: dict(slots) for bucket, slots in rates.items()}
        for model, rates in source.items()
    }
    if local >= PRO_FLASH_ROUTE_AT:
        table["deepseek-v4-pro"] = {
            bucket: dict(slots)
            for bucket, slots in PRICING["deepseek-v4-flash"].items()
        }
    return table


def resolve_rate(model: str, bucket: str, dt: datetime) -> float:
    local = _aware(dt).astimezone(CN_TZ)
    normalized_model = normalize_model(model)
    table = pricing_for_time(local)
    rates = table.get(normalized_model) or table["deepseek-v4-flash"]
    peak = _is_legacy_peak(local) if local < FLASH_PRICE_EFFECTIVE_AT else is_peak(local)
    slot = "peak" if peak else "offpeak"
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
