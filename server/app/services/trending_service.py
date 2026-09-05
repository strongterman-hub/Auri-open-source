from __future__ import annotations

import logging
import time

from app.web.client import SearchResult, WebSearchClient


logger = logging.getLogger("auri.trending")


class TrendingService:
    """Fetches trending web items once and reuses them until the cache expires."""

    def __init__(
        self,
        search_client: WebSearchClient,
        query: str,
        max_results: int = 6,
        cache_seconds: int = 3600,
    ) -> None:
        self.search_client = search_client
        self.query = query
        self.max_results = max_results
        self.cache_seconds = cache_seconds
        self._cache: list[SearchResult] = []
        self._cached_at: float | None = None

    async def items(self) -> list[SearchResult]:
        now = time.monotonic()
        if (
            self._cached_at is not None
            and self._cache
            and (now - self._cached_at) < self.cache_seconds
        ):
            return self._cache

        try:
            self._cache = await self.search_client.search(
                self.query, self.max_results
            )
        except Exception:  # noqa: BLE001 - a failed fetch must not crash the scheduler
            logger.exception("trending search failed")
            self._cache = []
        self._cached_at = now
        return self._cache
