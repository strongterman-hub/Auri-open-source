from __future__ import annotations

import logging
from typing import Any

import httpx

from app.observation.models import Observation, ObservationSource
from app.services.observation_service import ObservationService


logger = logging.getLogger("auri.weather")


class WeatherService:
    """Fetches current weather from Open-Meteo and emits change observations.

    The provider is intentionally dependency-light: Open-Meteo needs no API key
    and returns the fields the proactive engine can reason over directly. A new
    observation is written only when the current value differs from the last
    recorded weather observation for the same user, which keeps the observation
    layer from accumulating near-duplicate rows every tick.
    """

    endpoint = "https://api.open-meteo.com/v1/forecast"
    geocoding_endpoint = "https://geocoding-api.open-meteo.com/v1/search"
    reverse_geocoding_endpoint = "https://api.bigdatacloud.net/data/reverse-geocode-client"

    def __init__(
        self,
        observation_service: ObservationService,
        *,
        latitude: float | None = None,
        longitude: float | None = None,
        timezone: str = "Asia/Shanghai",
        min_temperature_delta: float = 2.0,
        min_humidity_delta: float = 10.0,
        client: httpx.AsyncClient | None = None,
        location_provider: Any | None = None,
    ) -> None:
        self.observation_service = observation_service
        self.latitude = latitude
        self.longitude = longitude
        self.timezone = timezone
        self.min_temperature_delta = min_temperature_delta
        self.min_humidity_delta = min_humidity_delta
        self._client = client
        self.location_provider = location_provider

    async def fetch_current(
        self,
        latitude: float,
        longitude: float,
    ) -> dict[str, Any]:
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
            ),
            "timezone": self.timezone,
        }
        response = await self._get(params)
        response.raise_for_status()
        data = response.json()
        current = data.get("current") or {}
        if not current:
            return {}
        return {
            "latitude": latitude,
            "longitude": longitude,
            "temperature_2m": current.get("temperature_2m"),
            "relative_humidity_2m": current.get("relative_humidity_2m"),
            "weather_code": current.get("weather_code"),
            "wind_speed_10m": current.get("wind_speed_10m"),
        }

    async def fetch_forecast(
        self,
        latitude: float,
        longitude: float,
        forecast_days: int = 3,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """Fetch current conditions plus a daily forecast for the next days."""
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": (
                "temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
            ),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max"
            ),
            "timezone": timezone or self.timezone,
            "forecast_days": max(1, min(forecast_days, 16)),
        }
        response = await self._get(params)
        response.raise_for_status()
        data = response.json()
        return {
            "latitude": latitude,
            "longitude": longitude,
            "timezone": data.get("timezone", timezone or self.timezone),
            "current": data.get("current") or {},
            "daily": data.get("daily") or {},
        }

    async def geocode(self, name: str) -> dict[str, Any] | None:
        """Resolve a free-form place name to coordinates using Open-Meteo."""
        name = (name or "").strip()
        if not name:
            return None

        params = {
            "name": name,
            "count": 1,
            "language": "zh",
            "format": "json",
        }
        response = await self._get_url(self.geocoding_endpoint, params)
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            return None

        first = results[0]
        latitude = first.get("latitude")
        longitude = first.get("longitude")
        if latitude is None or longitude is None:
            return None

        return {
            "name": first.get("name") or name,
            "latitude": float(latitude),
            "longitude": float(longitude),
            "timezone": first.get("timezone") or self.timezone,
            "country": first.get("country"),
            "admin1": first.get("admin1"),
        }

    async def reverse_geocode(
        self,
        latitude: float,
        longitude: float,
    ) -> dict[str, Any] | None:
        """Resolve coordinates to a human-readable place using BigDataCloud."""
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "localityLanguage": "zh",
        }
        response = await self._get_url(self.reverse_geocoding_endpoint, params)
        response.raise_for_status()
        data = response.json()
        if not data:
            return None
        return {
            "city": data.get("city") or data.get("locality"),
            "region": data.get("principalSubdivision"),
            "country": data.get("countryName"),
            "country_code": data.get("countryCode"),
            "latitude": data.get("latitude"),
            "longitude": data.get("longitude"),
        }

    async def update(
        self,
        user_id: str,
        agent_id: str = "default",
    ) -> Observation | None:
        location = self._location_for(user_id, agent_id)
        if location is None:
            return None
        current = await self.fetch_current(*location)
        if not current:
            return None

        previous = self._latest_weather(user_id, agent_id)
        if previous is not None and not self._changed(previous.payload, current):
            return None

        return self.observation_service.ingest_event(
            user_id,
            agent_id,
            ObservationSource.weather,
            "weather_changed",
            current,
        )

    def _location_for(
        self,
        user_id: str,
        agent_id: str,
    ) -> tuple[float, float] | None:
        if self.location_provider is not None:
            location = self.location_provider.location(user_id, agent_id)
            if location is not None:
                return float(location[0]), float(location[1])
        if self.latitude is not None and self.longitude is not None:
            return float(self.latitude), float(self.longitude)
        return None

    def _latest_weather(
        self,
        user_id: str,
        agent_id: str,
    ) -> Observation | None:
        results = self.observation_service.query(
            user_id,
            agent_id,
            source=ObservationSource.weather,
            limit=1,
        )
        return results[0] if results else None

    def _changed(self, previous: dict[str, Any], current: dict[str, Any]) -> bool:
        if current.get("weather_code") != previous.get("weather_code"):
            return True

        def numeric(value: Any) -> float | None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        previous_temp = numeric(previous.get("temperature_2m"))
        current_temp = numeric(current.get("temperature_2m"))
        if (
            previous_temp is not None
            and current_temp is not None
            and abs(current_temp - previous_temp) >= self.min_temperature_delta
        ):
            return True

        previous_humidity = numeric(previous.get("relative_humidity_2m"))
        current_humidity = numeric(current.get("relative_humidity_2m"))
        if (
            previous_humidity is not None
            and current_humidity is not None
            and abs(current_humidity - previous_humidity) >= self.min_humidity_delta
        ):
            return True

        return False

    async def _get(self, params: dict[str, Any]) -> httpx.Response:
        return await self._get_url(self.endpoint, params)

    async def _get_url(self, url: str, params: dict[str, Any]) -> httpx.Response:
        if self._client is not None:
            return await self._client.get(url, params=params)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.get(url, params=params)
