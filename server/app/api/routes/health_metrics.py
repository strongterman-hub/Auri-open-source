from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.schemas.health import (
    HealthMetricsResponse,
    HealthSyncRequest,
    HealthSyncResponse,
    SleepScoresResponse,
)

router = APIRouter()


@router.post("/sync", response_model=HealthSyncResponse)
async def sync_health(
    payload: HealthSyncRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> HealthSyncResponse:
    payload.timezone = container.timezone_resolver.get(current_user.id)
    synced = container.health_service.sync(current_user.id, payload)
    return HealthSyncResponse(synced=synced)


@router.get("/metrics", response_model=HealthMetricsResponse)
async def get_health_metrics(
    container: ContainerDep,
    current_user: CurrentUserDep,
    from_day: str = Query(...),
    to_day: str = Query(...),
) -> HealthMetricsResponse:
    timezone = container.timezone_resolver.get(current_user.id)
    metrics, samples = container.health_service.get_metrics(
        current_user.id, from_day, to_day, timezone
    )
    return HealthMetricsResponse(metrics=metrics, samples=samples)


@router.get("/sleep-scores", response_model=SleepScoresResponse)
async def get_sleep_scores(
    container: ContainerDep,
    current_user: CurrentUserDep,
    from_day: str = Query(...),
    to_day: str = Query(...),
) -> SleepScoresResponse:
    scores = container.health_service.get_sleep_scores(
        current_user.id,
        from_day,
        to_day,
        container.timezone_resolver.get(current_user.id),
    )
    return SleepScoresResponse(
        visible=container.settings.sleep_dual_score_visible,
        scores=scores,
    )
