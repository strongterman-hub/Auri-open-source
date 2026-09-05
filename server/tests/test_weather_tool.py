from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.agent.tools import WeatherTool


class FakePresence:
    def __init__(
        self,
        location: tuple[float, float] | None,
        reported_at: datetime | None = None,
    ) -> None:
        self._location = location
        self.reported_at = reported_at or datetime.now(timezone.utc)

    def location_reading(self, user_id: str, agent_id: str = "default"):
        if self._location is None:
            return None
        return SimpleNamespace(
            latitude=self._location[0],
            longitude=self._location[1],
            reported_at=self.reported_at,
        )


class FakeWeatherService:
    def __init__(self, geocode_result: dict | None = None) -> None:
        self.geocode_result = geocode_result
        self.last_latitude: float | None = None
        self.last_longitude: float | None = None

    async def geocode(self, name: str):
        return self.geocode_result

    async def fetch_forecast(
        self,
        latitude: float,
        longitude: float,
        forecast_days: int = 3,
        timezone: str | None = None,
    ):
        self.last_latitude = latitude
        self.last_longitude = longitude
        return {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": timezone or "Asia/Shanghai",
            "current": {"temperature_2m": 28.0, "weather_code": 1},
            "daily": {
                "time": ["2026-08-23", "2026-08-24"],
                "weather_code": [1, 3],
                "temperature_2m_max": [33.0, 30.0],
                "temperature_2m_min": [26.0, 24.0],
                "precipitation_probability_max": [10, 40],
            },
        }


def test_weather_tool_uses_presence_location() -> None:
    tool = WeatherTool(
        FakeWeatherService(),
        FakePresence((31.2, 121.4)),
        "u1",
        "default",
        fallback_latitude=None,
        fallback_longitude=None,
    )
    payload = json.loads(asyncio.run(tool.execute()))

    assert payload["daily"][0]["date"] == "2026-08-23"
    assert payload["location_context"]["source"] == "device_report"
    assert payload["location_context"]["is_user_current_location"] is True


def test_weather_tool_uses_named_location() -> None:
    service = FakeWeatherService(
        geocode_result={
            "name": "北京",
            "latitude": 39.9042,
            "longitude": 116.4074,
            "timezone": "Asia/Shanghai",
        }
    )
    tool = WeatherTool(
        service,
        FakePresence(None),
        "u1",
        "default",
        fallback_latitude=None,
        fallback_longitude=None,
    )
    payload = json.loads(asyncio.run(tool.execute(location="北京")))

    assert payload["location"] == "北京"
    assert service.last_latitude == 39.9042
    assert service.last_longitude == 116.4074
    assert payload["daily"][0]["date"] == "2026-08-23"
    assert payload["daily"][0]["max_c"] == 33.0
    assert payload["location_context"]["source"] == "named_place"


def test_weather_tool_falls_back_to_coordinates() -> None:
    tool = WeatherTool(
        FakeWeatherService(),
        FakePresence(None),
        "u1",
        "default",
        fallback_latitude=39.9,
        fallback_longitude=116.4,
    )
    payload = json.loads(asyncio.run(tool.execute()))

    assert payload["daily"][0]["date"] == "2026-08-23"
    assert payload["location_context"]["source"] == "configured_fallback"
    assert payload["location_context"]["is_user_current_location"] is False


def test_weather_tool_does_not_use_expired_gps_as_current_location() -> None:
    presence = FakePresence(
        (31.2, 121.4),
        reported_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )
    service = FakeWeatherService()
    tool = WeatherTool(
        service,
        presence,
        "u1",
        "default",
        fallback_latitude=39.9,
        fallback_longitude=116.4,
    )

    payload = json.loads(asyncio.run(tool.execute()))

    assert service.last_latitude == 39.9
    assert service.last_longitude == 116.4
    assert payload["location_context"]["source"] == "configured_fallback"
    assert payload["location_context"]["is_user_current_location"] is False
