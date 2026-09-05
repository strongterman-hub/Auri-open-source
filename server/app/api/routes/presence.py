from __future__ import annotations

import logging

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import ContainerDep, CurrentUserDep

router = APIRouter(prefix="/presence", tags=["presence"])
logger = logging.getLogger("auri.presence")


class HeartbeatResponse(BaseModel):
    status: str = "ok"


class HeartbeatRequest(BaseModel):
    timezone: str | None = None


class LocationUpdateRequest(BaseModel):
    latitude: float
    longitude: float
    timezone: str | None = None
    request_id: str | None = None


class LocationUpdateResponse(BaseModel):
    status: str = "ok"


@router.post("/heartbeat", response_model=HeartbeatResponse)
async def heartbeat(
    container: ContainerDep,
    current_user: CurrentUserDep,
    payload: HeartbeatRequest | None = None,
) -> HeartbeatResponse:
    container.presence_service.heartbeat(current_user.id, "default")
    if payload is not None and payload.timezone:
        container.timezone_resolver.set(current_user.id, payload.timezone)
    # Run onboarding synchronously so the client can immediately fetch the
    # generated welcome message from the proactive inbox in the next request.
    try:
        await container.proactive_engine.maybe_onboard(
            current_user.id,
            "default",
        )
    except Exception:
        logger.exception("proactive onboarding on heartbeat failed")
    return HeartbeatResponse()


@router.post("/location", response_model=LocationUpdateResponse)
async def update_location(
    payload: LocationUpdateRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> LocationUpdateResponse:
    container.presence_service.update_location(
        current_user.id,
        "default",
        payload.latitude,
        payload.longitude,
    )
    if payload.timezone:
        container.timezone_resolver.set(current_user.id, payload.timezone)
    if payload.request_id:
        container.presence_service.resolve_location_request(payload.request_id)
    return LocationUpdateResponse()
