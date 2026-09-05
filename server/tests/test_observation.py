from __future__ import annotations

from pathlib import Path

from app.memory.models import OriginClass
from app.observation.models import Observation, ObservationSource
from app.observation.store import ObservationStore
from app.schemas.health import HealthMetricIn, HealthSampleIn
from app.services.observation_service import ObservationService


def test_observation_store_roundtrip(tmp_dir: Path) -> None:
    store = ObservationStore(tmp_dir / "memory.db")
    store.add(
        Observation(
            user_id="u1",
            source=ObservationSource.health,
            kind="STEPS",
            origin=OriginClass.untrusted,
            payload={"day": "2026-08-19", "value1": 1000.0},
        )
    )

    results = store.query("u1", source=ObservationSource.health, kind="STEPS")

    assert len(results) == 1
    assert results[0].payload["value1"] == 1000.0
    assert results[0].origin is OriginClass.untrusted


def test_ingest_health_produces_reference_observations(tmp_dir: Path) -> None:
    store = ObservationStore(tmp_dir / "memory.db")
    service = ObservationService(store)
    metrics = [
        HealthMetricIn(metric_type="STEPS", day="2026-08-19", value1=1000.0),
        HealthMetricIn(metric_type="SLEEP", day="2026-08-19", value1=420.0),
    ]
    samples = [
        HealthSampleIn(
            metric_type="HEART_RATE",
            day="2026-08-19",
            bucket_start="2026-08-19T00:00:00Z",
            bucket_end="2026-08-19T00:15:00Z",
            value1=70.0,
        )
    ]

    observations = service.ingest_health(
        "u1", "default", "2026-08-01", "2026-08-19", metrics, samples
    )

    kinds = {observation.kind for observation in observations}
    assert {"sync", "STEPS", "SLEEP", "HEART_RATE"} <= kinds
    assert all(observation.origin is OriginClass.untrusted for observation in observations)
    assert all(observation.source is ObservationSource.health for observation in observations)


def test_repeated_health_sync_replaces_same_logical_observations(tmp_dir: Path) -> None:
    store = ObservationStore(tmp_dir / "memory.db")
    service = ObservationService(store)
    metrics = [HealthMetricIn(metric_type="STEPS", day="2026-08-19", value1=1000.0)]
    samples = [
        HealthSampleIn(
            metric_type="HEART_RATE",
            day="2026-08-19",
            bucket_start="2026-08-19T00:00:00Z",
            bucket_end="2026-08-19T00:15:00Z",
            value1=70.0,
        )
    ]

    service.ingest_health("u1", "default", "2026-08-01", "2026-08-19", metrics, samples)
    service.ingest_health("u1", "default", "2026-08-01", "2026-08-19", metrics, samples)

    observations = store.query("u1")
    assert len(observations) == 3
    assert {item.kind for item in observations} == {"sync", "STEPS", "HEART_RATE"}


def test_ingest_event_supports_weather_and_phone_state(tmp_dir: Path) -> None:
    store = ObservationStore(tmp_dir / "memory.db")
    service = ObservationService(store)

    weather = service.ingest_event(
        "u1",
        "default",
        ObservationSource.weather,
        "weather_changed",
        {"condition": "rain", "city": "shanghai"},
    )
    phone = service.ingest_event(
        "u1",
        "default",
        ObservationSource.phone_state,
        "location_changed",
        {"distance_km": 3.2},
    )

    assert weather.source is ObservationSource.weather
    assert phone.source is ObservationSource.phone_state
    assert store.query("u1", source=ObservationSource.weather)[0].kind == "weather_changed"
