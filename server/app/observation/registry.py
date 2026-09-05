from __future__ import annotations

from abc import ABC, abstractmethod

from app.observation.models import Observation, ObservationSource


class ObservationSourceProvider(ABC):
    """Interface for turning an external source's data into observations."""

    source: ObservationSource

    @abstractmethod
    async def ingest(self, user_id: str, agent_id: str, **kwargs: object) -> list[Observation]:
        """Produce observations from a source payload."""


class NullObservationProvider(ObservationSourceProvider):
    """Placeholder for sources whose real provider ships in a later window."""

    def __init__(self, source: ObservationSource) -> None:
        self.source = source

    async def ingest(self, user_id: str, agent_id: str, **kwargs: object) -> list[Observation]:
        return []


class ObservationSourceRegistry:
    def __init__(self) -> None:
        self._providers: dict[ObservationSource, ObservationSourceProvider] = {}

    def register(self, provider: ObservationSourceProvider) -> None:
        self._providers[provider.source] = provider

    def get(self, source: ObservationSource) -> ObservationSourceProvider | None:
        return self._providers.get(source)
