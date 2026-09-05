from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.dependencies import ContainerDep, CurrentUserDep

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceRegisterRequest(BaseModel):
    token: str


class DeviceRegisterResponse(BaseModel):
    registered: bool


class DeviceUnregisterResponse(BaseModel):
    unregistered: bool


@router.post("", response_model=DeviceRegisterResponse)
async def register_device(
    payload: DeviceRegisterRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> DeviceRegisterResponse:
    registered = container.presence_service.register_device(
        current_user.id,
        "default",
        payload.token,
    )
    return DeviceRegisterResponse(registered=registered)


@router.delete("/{token}", response_model=DeviceUnregisterResponse)
async def unregister_device(
    token: str,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> DeviceUnregisterResponse:
    unregistered = container.presence_service.unregister_device(
        current_user.id,
        "default",
        token,
    )
    if not unregistered:
        raise HTTPException(status_code=404, detail="设备不存在")
    return DeviceUnregisterResponse(unregistered=unregistered)
