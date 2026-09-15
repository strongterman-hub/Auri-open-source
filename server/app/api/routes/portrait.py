from __future__ import annotations

from fastapi import APIRouter

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.memory.models import MemoryScope
from app.schemas.portrait import (
    PortraitCurrentResponse,
    PortraitSettingsRequest,
    PortraitSettingsResponse,
)

router = APIRouter(prefix="/portrait", tags=["portrait"])


@router.get("/current", response_model=PortraitCurrentResponse)
async def get_current_portrait(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> PortraitCurrentResponse:
    scope = MemoryScope(user_id=current_user.id, agent_id="default")
    service = container.portrait_service
    enabled = service.is_enabled(scope)
    state = service.current(scope) if enabled else service.fallback_state(scope)
    effective_variant, image_url, image_url_small = service.image_urls(state)
    refresh_seconds = max(60, int(container.settings.portrait_refresh_seconds or 900))
    return PortraitCurrentResponse(
        presentation=state.presentation,
        variant=effective_variant,
        image_url=image_url,
        image_url_small=image_url_small,
        time_slot=state.time_slot,
        mood=state.mood,
        stage=state.stage,
        reason=state.reason,
        resolved_at=state.resolved_at,
        expires_in_seconds=refresh_seconds,
        enabled=enabled,
    )


@router.get("/settings", response_model=PortraitSettingsResponse)
async def get_portrait_settings(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> PortraitSettingsResponse:
    scope = MemoryScope(user_id=current_user.id, agent_id="default")
    service = container.portrait_service
    preference = service.get_settings(scope)
    return PortraitSettingsResponse(
        smart_background_enabled=preference.smart_background_enabled,
        available=service.feature_available(scope),
    )


@router.put("/settings", response_model=PortraitSettingsResponse)
async def update_portrait_settings(
    payload: PortraitSettingsRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> PortraitSettingsResponse:
    scope = MemoryScope(user_id=current_user.id, agent_id="default")
    service = container.portrait_service
    preference = service.update_settings(
        scope,
        smart_background_enabled=payload.smart_background_enabled,
    )
    return PortraitSettingsResponse(
        smart_background_enabled=preference.smart_background_enabled,
        available=service.feature_available(scope),
    )
