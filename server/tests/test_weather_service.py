from __future__ import annotations

import asyncio
from typing import Any

from app.observation.store import ObservationStore
from app.services.observation_service import ObservationService
from app.services.presence_service import PresenceService
from app.services.weather_service import WeatherService


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self.payload


class FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def get(self, url: str, params: dict[str, Any] | None = None) -> FakeResponse:
        self.last_params = params
        return FakeResponse(self.payload)


def test_weather_service_writes_first_and_changed_observations(tmp_dir) -> None:
    observation_service = ObservationService(ObservationStore(tmp_dir / "memory.db"))
    client = FakeClient(
        {
            "current": {
                "temperature_2m": 22.0,
                "relative_humidity_2m": 50,
                "weather_code": 1,
                "wind_speed_10m": 3.0,
            }
        }
    )
    service = WeatherService(
        observation_service,
        latitude=31.2304,
        longitude=121.4737,
        client=client,
    )

    first = asyncio.run(service.update("u1"))

    assert first is not None
    assert first.kind == "weather_changed"
    assert first.payload["temperature_2m"] == 22.0

    unchanged = asyncio.run(service.update("u1"))
    assert unchanged is None

    client.payload["current"]["weather_code"] = 3
    changed = asyncio.run(service.update("u1"))
    assert changed is not None
    assert changed.payload["weather_code"] == 3


def test_weather_service_prefers_gps_location_over_fallback(tmp_dir) -> None:
    observation_service = ObservationService(ObservationStore(tmp_dir / "memory.db"))
    client = FakeClient(
        {
            "current": {
                "temperature_2m": 18.0,
                "relative_humidity_2m": 60,
                "weather_code": 2,
                "wind_speed_10m": 2.0,
            }
        }
    )
    presence = PresenceService()
    presence.update_location("u1", "default", 31.5000, 121.6000)
    service = WeatherService(
        observation_service,
        latitude=1.0,
        longitude=2.0,
        client=client,
        location_provider=presence,
    )

    observation = asyncio.run(service.update("u1"))

    assert observation is not None
    assert observation.payload["latitude"] == 31.5
    assert observation.payload["longitude"] == 121.6
    assert client.last_params["latitude"] == 31.5
    assert client.last_params["longitude"] == 121.6


def test_weather_service_geocodes_place_name(tmp_dir) -> None:
    observation_service = ObservationService(ObservationStore(tmp_dir / "memory.db"))
    client = FakeClient(
        {
            "results": [
                {
                    "name": "北京",
                    "latitude": 39.9042,
                    "longitude": 116.4074,
                    "timezone": "Asia/Shanghai",
                    "country": "中国",
                    "admin1": "北京市",
                }
            ]
        }
    )
    service = WeatherService(
        observation_service,
        latitude=1.0,
        longitude=2.0,
        client=client,
    )

    result = asyncio.run(service.geocode("北京"))

    assert result is not None
    assert result["name"] == "北京"
    assert result["latitude"] == 39.9042
    assert result["longitude"] == 116.4074
    assert client.last_params["name"] == "北京"
