from __future__ import annotations

import asyncio
from typing import Any

from app.integrations.xiaomi.client import XiaomiCloudClient


TZ_OFFSET = 28800
DAY_START = 1786982400  # 2026-08-18 00:00:00 Asia/Shanghai


def _record(sid: str, time: int, value: dict, update_time: int = 0) -> dict[str, Any]:
    return {
        "sid": sid,
        "time": time,
        "zone_offset": TZ_OFFSET,
        "update_time": update_time,
        "value": value,
    }


class StubClient(XiaomiCloudClient):
    def __init__(self, keys: dict[str, list[dict]] | None = None) -> None:
        super().__init__(user_id="u", pass_token="t", region="cn")
        self._keys = keys or {}
        self._connected = True
        self._client = object()

    async def _fetch_key(
        self, key: str, start_date: str, end_date: str, region: str | None = None
    ) -> list[dict]:
        return self._keys.get(key, [])


async def _collect(agen) -> list:
    return [item async for item in agen]


def test_select_primary_sid_prefers_device_over_generated() -> None:
    client = StubClient()
    device = _record("764271150", DAY_START, {"steps": 10}, update_time=200)
    generated = _record("hlth.gen_123", DAY_START, {"steps": 12}, update_time=100)
    assert client._select_primary_sid([device, generated]) == "764271150"
    assert client._select_primary_sid([generated]) == "hlth.gen_123"
    assert client._select_primary_sid([]) is None


def test_select_primary_sid_can_prefer_xiaomi_aggregate() -> None:
    client = StubClient()
    device = _record("764271150", DAY_START, {"steps": 10}, update_time=200)
    generated = _record("hlth.gen_123", DAY_START, {"steps": 12}, update_time=100)
    assert (
        client._select_primary_sid([device, generated], prefer_generated=True)
        == "hlth.gen_123"
    )


def test_select_primary_sid_latest_device_wins() -> None:
    client = StubClient()
    older = _record("111", DAY_START, {"steps": 10}, update_time=100)
    newer = _record("222", DAY_START, {"steps": 10}, update_time=300)
    assert client._select_primary_sid([older, newer]) == "222"


def test_sleep_stage_name_maps_verified_states() -> None:
    client = StubClient()
    assert client._sleep_stage_name(2) == "deep"
    assert client._sleep_stage_name(3) == "light"
    assert client._sleep_stage_name(4) == "rem"
    assert client._sleep_stage_name(5) == "awake"


def test_iter_daily_activity_resolves_sources_per_metric() -> None:
    client = StubClient(
        {
            "steps": [
                _record(
                    "764271150",
                    DAY_START,
                    {"steps": 100, "distance": 1000, "calories": 50},
                    update_time=200,
                ),
                _record(
                    "hlth.gen_123",
                    DAY_START,
                    {"steps": 120, "distance": 1100, "calories": 60},
                    update_time=100,
                ),
            ],
            "calories": [
                _record("764271150", DAY_START, {"calories": 500}, update_time=200),
                _record("hlth.gen_123", DAY_START, {"calories": 600}, update_time=100),
            ],
        }
    )
    activities = asyncio.run(
        _collect(client.iter_daily_activity("2026-08-18", "2026-08-18"))
    )
    assert len(activities) == 1
    activity = activities[0]
    assert activity.steps == 120
    assert activity.distance_m == 1100.0
    assert activity.active_kcal == 500.0
    assert activity.steps_source == "xiaomi_aggregate"
    assert activity.distance_source == "xiaomi_aggregate"
    assert activity.calories_source == "xiaomi_device"
    assert activity.steps_source_updated_at == 100_000
    assert activity.calories_source_updated_at == 200_000


def test_iter_sleep_sessions_maps_awake_and_uses_official_duration() -> None:
    start_deep = DAY_START
    end_deep = start_deep + 3600
    start_awake = end_deep
    end_awake = start_awake + 120
    client = StubClient(
        {
            "sleep": [
                {
                    "sid": "764271150",
                    "time": start_deep,
                    "zone_offset": TZ_OFFSET,
                    "update_time": 0,
                    "value": {
                        "items": [
                            {"state": 2, "start_time": start_deep, "end_time": end_deep},
                            {"state": 5, "start_time": start_awake, "end_time": end_awake},
                        ],
                        "sleep_deep_duration": 60,
                        "sleep_light_duration": 0,
                        "sleep_rem_duration": 0,
                        "sleep_awake_duration": 2,
                    },
                }
            ],
        }
    )
    sessions = asyncio.run(
        _collect(client.iter_sleep_sessions("2026-08-18", "2026-08-18"))
    )
    assert len(sessions) == 1
    session = sessions[0]
    stages = {stage.stage: stage for stage in session.stages}
    assert "awake" in stages
    assert "deep" in stages
    assert session.time_awake_minutes == 2
