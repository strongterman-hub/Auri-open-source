from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import ContainerDep, CurrentUserDep

router = APIRouter(prefix="/profile", tags=["profile"])


class TimezoneUpdateRequest(BaseModel):
    timezone: str


class TimezoneResponse(BaseModel):
    timezone: str


@router.get("/timezone", response_model=TimezoneResponse)
async def get_timezone(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> TimezoneResponse:
    return TimezoneResponse(
        timezone=container.timezone_resolver.get(current_user.id),
    )


@router.put("/timezone", response_model=TimezoneResponse)
async def set_timezone(
    payload: TimezoneUpdateRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> TimezoneResponse:
    timezone = container.timezone_resolver.set(current_user.id, payload.timezone)
    return TimezoneResponse(timezone=timezone)
