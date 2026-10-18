from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from zoneinfo import ZoneInfo
from app.reports.insights import add_insight_candidates


METRICS = {
    "STEPS": ("步数", "步", 1), "DISTANCE": ("活动距离", "公里", .001),
    "CALORIES": ("活动消耗", "千卡", 1),
    "RESTING_HEART_RATE": ("静息心率", "次/分", 1),
    "HEART_RATE": ("记录心率", "次/分", 1), "SPO2": ("设备血氧", "%", 1),
    "STRESS": ("设备压力指数", "分", 1), "WEIGHT": ("体重", "公斤", 1),
    "BODY_FAT": ("体脂率", "%", 1),
}


def recent_week(now: datetime, timezone_name: str):
    today = now.astimezone(ZoneInfo(timezone_name)).date()
    return today - timedelta(days=7), today - timedelta(days=1)


def number(value):
    if value is None or not math.isfinite(float(value)):
        return None
    return round(float(value), 1)


def display(value, unit=""):
    value = number(value)
    return "暂无记录" if value is None else f"{value:g}{unit}"


def updated_time(records, zone):
    times = []
    for record in records:
        try:
            stamp = float(record.updated_at or 0)
            if not math.isfinite(stamp) or stamp <= 0:
                continue
            # Xiaomi timestamps are milliseconds; imported records may use seconds.
            # Normalize each record before comparing mixed sources.
            if stamp >= 100_000_000_000:
                stamp /= 1000
            times.append(datetime.fromtimestamp(stamp, zone))
        except (AttributeError, TypeError, ValueError, OverflowError, OSError):
            continue
    return max(times).strftime("%Y-%m-%d %H:%M") if times else "未提供同步时间"


def collect_facts(health, user_id, start, end, timezone_name, scores_visible=True, *, as_of=None):
    local_now = (as_of or datetime.now(timezone.utc)).astimezone(ZoneInfo(timezone_name))
    if end >= local_now.date():
        raise ValueError('report_period_must_end_before_today')
    previous = start - timedelta(days=7)
    metrics, samples = health.get_metrics(user_id, previous.isoformat(), end.isoformat(), timezone_name)
    days = [(start + timedelta(days=i)).isoformat() for i in range(7)]
    old_days = [(previous + timedelta(days=i)).isoformat() for i in range(7)]
    facts = {"period.start": start.isoformat(), "period.end": end.isoformat(), "period.timezone": timezone_name}
    facts.update({f'DAY.{day}': day for day in [*old_days, *days]})
    series, details, previous_series = {}, {}, {}

    def add(key, label, unit, values, old_values, *, comparable=True):
        valid = [v for v in values if v is not None]
        prior = [v for v in old_values if v is not None]
        if not valid:
            return
        series[key] = {"label": label, "unit": unit, "days": days, "values": values}
        if key in {'STEPS', 'MAIN_SLEEP', 'RESTING_HEART_RATE'}:
            previous_series[key] = {"unit": unit, "days": old_days, "values": old_values}
        details[key] = {"label": label, "unit": unit, "valid_days": len(valid), "previous_valid_days": len(prior)}
        facts.update({f"{key}.days": f"{len(valid)}/7天", f"{key}.avg": display(mean(valid), unit),
                      f"{key}.min": display(min(valid), unit), f"{key}.max": display(max(valid), unit)})
        # Totals are meaningful only for additive measurements, not heart rate/scores.
        if key in {"STEPS", "DISTANCE", "CALORIES", "NAP", "WORKOUT"}:
            facts[f"{key}.total"] = display(sum(valid), unit)
        for day, value in zip(days, values):
            facts[f"{key}.{day}"] = display(value, unit)
        if comparable and len(valid) >= 5 and len(prior) >= 5:
            delta = mean(valid) - mean(prior)
            facts[f"{key}.previous_avg"] = display(mean(prior), unit)
            facts[f"{key}.delta"] = display(delta, unit)
            facts[f"{key}.previous_days"] = f"{len(prior)}/7天"
            if mean(prior):
                facts[f"{key}.delta_pct"] = display(delta / mean(prior) * 100, "%")

    for key, (label, unit, factor) in METRICS.items():
        rows = {m.day: number(m.value1) for m in metrics if m.metric_type == key}
        vals = lambda ds: [None if rows.get(d) is None else number(rows[d] * factor) for d in ds]
        add(key, label, unit, vals(days), vals(old_days), comparable=key not in {"WEIGHT", "BODY_FAT"})

    zone = ZoneInfo(timezone_name)
    if health.sleep_score_service is not None:
        all_sleep = health.store.list_samples(user_id, {"SLEEP_SESSION", "SLEEP_STAGE", "HEART_RATE", "RESTING_HEART_RATE", "SLEEP_HRV"})
        nights = health.sleep_score_service._build_nights(all_sleep, zone)
        rows = {n.sleep_day: number(n.asleep_minutes) for n in nights}
        add("MAIN_SLEEP", "主睡眠实际睡着时长", "分钟", [rows.get(d) for d in days], [rows.get(d) for d in old_days])
        current = [n for n in nights if n.sleep_day in days]
        if len(current) >= 3:
            # Noon-centred clock avoids midnight wrap-around (23:50 vs 00:10).
            bed = [((n.start_at.astimezone(zone).hour * 60 + n.start_at.astimezone(zone).minute - 720) % 1440) for n in current]
            facts["MAIN_SLEEP.bedtime_variation"] = display(pstdev(bed), "分钟")
            for n in current:
                facts[f"BEDTIME.{n.sleep_day}"] = n.start_at.astimezone(zone).strftime("%H:%M")
                facts[f"WAKE.{n.sleep_day}"] = n.end_at.astimezone(zone).strftime("%H:%M")

    # Only explicit nap sessions count. No inferred daily-total-minus-main naps.
    for key, sample_type, unit, label in [("NAP", "SLEEP_SESSION", "分钟", "明确记录的午睡实际时长"), ("WORKOUT", "WORKOUT", "分钟", "记录运动时长")]:
        rows = {}
        for s in samples:
            if s.metric_type != sample_type or (key == "NAP" and s.value4 != "nap"):
                continue
            value = number(s.value2 if key == "NAP" else s.value1)
            if value is not None and value >= 0:
                rows[s.day] = rows.get(s.day, 0) + value
        add(key, label, unit, [rows.get(d) for d in days], [rows.get(d) for d in old_days], comparable=False)
    if scores_visible:
        scores = health.get_sleep_scores(user_id, previous.isoformat(), end.isoformat(), timezone_name)
        for key, attr, label in [("SLEEP_SCORE", "sleep_health", "睡眠健康分"), ("RECOVERY_SCORE", "recovery", "恢复分")]:
            eligible = [s for s in scores if s.calibration.state == "calibrated" and getattr(s, attr).score is not None and getattr(s, attr).confidence >= 60]
            versions = {s.meta.algorithm_version for s in eligible}
            rows = {s.sleep_day: getattr(s, attr).score for s in eligible}
            add(key, label, "分", [rows.get(d) for d in days], [rows.get(d) for d in old_days], comparable=len(versions) == 1)

    facts["data.updated_at"] = updated_time([*metrics, *samples], zone)
    return add_insight_candidates({"facts": facts, "series": series, "previous_series": previous_series, "coverage": details,
            "rules": ["均值分母为有记录的有效日，缺失不等于零，数值零是有效记录而非无记录", "DAY.日期是日期标签；指标键.日期是当天测量值，不能当作日期", "主睡眠与午睡分开", "穿戴数据不能确诊或证明原因", "没有环比事实的指标不得声称比前期升降", "设备压力不等于本人主观压力", "睡眠与恢复评分分别解释"]})
