from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from app.memory.models import OriginClass
from app.observation.models import Observation, ObservationSource
from app.observation.store import ObservationStore
from app.schemas.health import HealthMetricIn, HealthSampleIn


def _day_dt(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def _stable_observation_id(*parts: str) -> str:
    raw = "\x1f".join(parts)
    return "obs_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()


class ObservationService:
    """Writes and queries the unified episodic observation layer."""

    def __init__(self, store: ObservationStore) -> None:
        self.store = store

    def ingest_health(
        self,
        user_id: str,
        agent_id: str,
        from_day: str,
        to_day: str,
        metrics: list[HealthMetricIn],
        samples: list[HealthSampleIn],
    ) -> list[Observation]:
        """Turn a Xiaomi sync result into compact, reference-style observations.

        Full metric/sample payloads remain in ``HealthStore``; the observation
        layer keeps a light summary (daily metric values and per-type counts) so
        consolidation and search can reason over it without duplicating raw data.
        """
        observations: list[Observation] = [
            Observation(
                id=_stable_observation_id(
                    user_id,
                    agent_id,
                    "health",
                    "sync",
                    from_day,
                    to_day,
                ),
                user_id=user_id,
                agent_id=agent_id,
                source=ObservationSource.health,
                origin=OriginClass.untrusted,
                kind="sync",
                supersession_key=f"health:sync:{from_day}:{to_day}",
                payload={
                    "from_day": from_day,
                    "to_day": to_day,
                    "metric_count": len(metrics),
                    "sample_count": len(samples),
                },
            )
        ]

        for metric in metrics:
            observations.append(
                Observation(
                    id=_stable_observation_id(
                        user_id,
                        agent_id,
                        "health",
                        "metric",
                        metric.metric_type,
                        metric.day,
                    ),
                    user_id=user_id,
                    agent_id=agent_id,
                    source=ObservationSource.health,
                    origin=OriginClass.untrusted,
                    kind=metric.metric_type,
                    observed_at=_day_dt(metric.day),
                    supersession_key=f"health:metric:{metric.metric_type}:{metric.day}",
                    payload={
                        "day": metric.day,
                        "value1": metric.value1,
                        "value2": metric.value2,
                        "value3": metric.value3,
                    },
                )
            )

        counts: dict[tuple[str, str], int] = {}
        for sample in samples:
            key = (sample.metric_type, sample.day)
            counts[key] = counts.get(key, 0) + 1
        for (sample_type, day), count in sorted(counts.items()):
            observations.append(
                Observation(
                    id=_stable_observation_id(
                        user_id,
                        agent_id,
                        "health",
                        "sample_count",
                        sample_type,
                        day,
                    ),
                    user_id=user_id,
                    agent_id=agent_id,
                    source=ObservationSource.health,
                    origin=OriginClass.untrusted,
                    kind=sample_type,
                    observed_at=_day_dt(day),
                    supersession_key=f"health:sample_count:{sample_type}:{day}",
                    payload={"day": day, "count": count},
                )
            )

        self.store.add_many(observations)
        return observations

    def ingest_event(
        self,
        user_id: str,
        agent_id: str,
        source: ObservationSource,
        kind: str,
        payload: dict,
        observed_at: datetime | None = None,
    ) -> Observation:
        """Write a single external event (schedule / phone state / weather)."""
        observation = Observation(
            user_id=user_id,
            agent_id=agent_id,
            source=source,
            origin=OriginClass.untrusted,
            kind=kind,
            payload=payload,
            observed_at=observed_at or datetime.now(timezone.utc),
        )
        self.store.add(observation)
        return observation

    def query(
        self,
        user_id: str,
        agent_id: str = "default",
        source: ObservationSource | None = None,
        kind: str | None = None,
        from_day: str | None = None,
        to_day: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[Observation]:
        return self.store.query(
            user_id=user_id,
            agent_id=agent_id,
            source=source,
            kind=kind,
            from_day=from_day,
            to_day=to_day,
            query=query,
            limit=limit,
        )
