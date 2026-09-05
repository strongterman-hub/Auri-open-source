from __future__ import annotations

import logging

from app.health.store import HealthStore
from app.health.sleep_score_service import SleepScoreService
from app.schemas.health import HealthMetricOut, HealthSampleOut, HealthSyncRequest, SleepScoreOut


logger = logging.getLogger("auri.sleep_score")


class HealthService:
    def __init__(
        self,
        store: HealthStore,
        sleep_score_service: SleepScoreService | None = None,
    ) -> None:
        self.store = store
        self.sleep_score_service = sleep_score_service

    def sync(self, user_id: str, payload: HealthSyncRequest) -> int:
        synced = self.store.sync(
            user_id=user_id,
            from_day=payload.from_day,
            to_day=payload.to_day,
            metric_types=payload.metric_types,
            metrics=payload.metrics,
            samples=payload.samples,
            tz=getattr(payload, "timezone", None) or "Asia/Shanghai",
        )
        if self.sleep_score_service is not None:
            try:
                self.sleep_score_service.recompute_all(
                    user_id,
                    getattr(payload, "timezone", None) or "Asia/Shanghai",
                )
            except Exception:
                # Dual scoring starts in shadow mode; a derived-score failure
                # must not roll back or disguise a successful raw-data sync.
                logger.exception("sleep score recompute failed for user %s", user_id)
        return synced

    def get_metrics(
        self,
        user_id: str,
        from_day: str,
        to_day: str,
        tz: str = "Asia/Shanghai",
    ) -> tuple[list[HealthMetricOut], list[HealthSampleOut]]:
        return self.store.get_metrics(user_id, from_day, to_day, tz)

    def get_sleep_scores(
        self,
        user_id: str,
        from_day: str,
        to_day: str,
        tz: str = "Asia/Shanghai",
    ) -> list[SleepScoreOut]:
        if self.sleep_score_service is None:
            return []
        if not self.sleep_score_service.score_store.has_scores(user_id):
            self.sleep_score_service.recompute_all(user_id, tz)
        return self.sleep_score_service.get_scores(user_id, from_day, to_day)
