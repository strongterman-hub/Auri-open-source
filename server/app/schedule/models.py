from __future__ import annotations

from datetime import date, datetime, timedelta, timezone as utc_timezone
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator


def localize(value: datetime, tz: str) -> datetime:
    """Reject ambiguous/nonexistent wall times instead of silently moving events."""
    zone = ZoneInfo(tz)
    if value.tzinfo is not None:
        raise ValueError("起止时间请使用日程时区的本地时间，不要附带 UTC 偏移")
    candidates = []
    for fold in (0, 1):
        aware = value.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(utc_timezone.utc).astimezone(zone).replace(tzinfo=None) == value:
            candidates.append(aware)
    if not candidates:
        raise ValueError("该时间在夏令时切换中不存在，请选择其他时间")
    if len({item.utcoffset() for item in candidates}) > 1:
        raise ValueError("该时间在夏令时切换中有歧义，请选择其他时间")
    return candidates[0]


class EventData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=160)
    starts_at: datetime
    ends_at: datetime
    timezone: str = "Asia/Shanghai"
    all_day: bool = False
    location: str = Field(default="", max_length=300)
    notes: str = Field(default="", max_length=3000)
    reminder_minutes: int | None = Field(default=None, ge=0, le=10080)
    repeat: Literal["none", "daily", "weekdays", "weekly"] = "none"
    repeat_until: date | None = None
    status: Literal["scheduled", "completed", "cancelled"] = "scheduled"

    @model_validator(mode="after")
    def valid(self):
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("无效的日程时区")
        start, end = localize(self.starts_at, self.timezone), localize(self.ends_at, self.timezone)
        if not (1970 <= start.year <= 2100 and 1970 <= end.year <= 2100):
            raise ValueError("日程年份应在 1970 到 2100 之间")
        if end.astimezone(utc_timezone.utc) <= start.astimezone(utc_timezone.utc):
            raise ValueError("结束时间必须晚于开始时间")
        duration = self.ends_at - self.starts_at
        if duration > timedelta(days=31):
            raise ValueError("单条日程最长 31 天")
        if self.all_day and (self.starts_at.time() != datetime.min.time() or self.ends_at.time() != datetime.min.time()):
            raise ValueError("全天日程需使用零点日期区间，结束日期不包含在内")
        if self.repeat != "none":
            if self.repeat_until and self.repeat_until < self.starts_at.date():
                raise ValueError("重复截止日期不能早于开始日期")
            if self.repeat == "weekdays" and self.starts_at.weekday() > 4:
                raise ValueError("工作日日程的首次日期应为周一至周五")
            if duration > timedelta(days=7 if self.repeat == "weekly" else 1):
                raise ValueError("日程时长不能超过重复间隔")
        elif self.repeat_until is not None:
            raise ValueError("不重复的日程无需重复截止日期")
        return self


class CreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=100)
    event: EventData
    allow_conflicts: bool = False


class UpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=100)
    version: int = Field(ge=1)
    scope: Literal["occurrence", "series"] = "series"
    occurrence_date: date | None = None
    changes: dict
    allow_conflicts: bool = False
    reset_exceptions: bool = False
