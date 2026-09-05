from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.proactive.models import TriggerType

router = APIRouter()


class LoginStartResponse(BaseModel):
    session_id: str
    qr_image_png_base64: str


class LoginStatusResponse(BaseModel):
    status: str
    error: str | None = None


class SyncResponse(BaseModel):
    synced: int
    metrics: int
    samples: int
    from_day: str
    to_day: str
    available_data_types: list[str] = Field(default_factory=list)


class StatusResponse(BaseModel):
    bound: bool
    last_sync_at: int | None = None
    available_data_types: list[str] = Field(default_factory=list)


@router.post("/login/start", response_model=LoginStartResponse)
async def start_login(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> LoginStartResponse:
    session_id, qr_png = await container.xiaomi_qr_login.start(current_user.id)
    return LoginStartResponse(session_id=session_id, qr_image_png_base64=qr_png)


@router.get("/login/status", response_model=LoginStatusResponse)
async def login_status(
    container: ContainerDep,
    current_user: CurrentUserDep,
    session_id: str = Query(...),
) -> LoginStatusResponse:
    result = container.xiaomi_qr_login.status(session_id)
    return LoginStatusResponse(status=result["status"], error=result.get("error"))


@router.post("/sync", response_model=SyncResponse)
async def sync_xiaomi(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> SyncResponse:
    result = await container.xiaomi_service.sync(current_user.id)
    # New health data is a proactive-messaging event source.
    await container.proactive_engine.evaluate_daily(
        current_user.id,
        "default",
        TriggerType.event,
    )
    return SyncResponse(**result)


@router.get("/status", response_model=StatusResponse)
async def get_xiaomi_status(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> StatusResponse:
    result = container.xiaomi_service.status(current_user.id)
    return StatusResponse(**result)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_xiaomi(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> None:
    container.xiaomi_service.disconnect(current_user.id)
