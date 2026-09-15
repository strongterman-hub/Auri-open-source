from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.memory.models import MemoryScope
from app.schemas.persona import (
    PersonaMeResponse,
    PersonaPresetsResponse,
    PersonaSelectionRequest,
    PersonaSelectionResponse,
)

router = APIRouter(prefix="/persona", tags=["persona"])


@router.get("/presets", response_model=PersonaPresetsResponse)
async def list_persona_presets(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> PersonaPresetsResponse:
    return PersonaPresetsResponse(presets=container.persona_service.list_presets())


@router.get("/me", response_model=PersonaMeResponse)
async def get_persona_me(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> PersonaMeResponse:
    scope = MemoryScope(user_id=current_user.id, agent_id="default")
    return PersonaMeResponse(
        preset=container.persona_service.resolve_preset(scope).model_dump(),
        relationship=container.persona_service.relationship_state(scope).model_dump(),
        user_selection_enabled=container.settings.persona_user_selection_enabled,
    )


@router.put("/me", response_model=PersonaSelectionResponse)
async def update_persona_me(
    payload: PersonaSelectionRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> PersonaSelectionResponse:
    if not container.settings.persona_user_selection_enabled:
        raise HTTPException(status_code=403, detail="用户人设选择尚未开放")
    scope = MemoryScope(user_id=current_user.id, agent_id="default")
    try:
        selection = container.persona_service.set_user_selection(
            scope,
            payload.preset_id,
            payload.overrides,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PersonaSelectionResponse(
        preset_id=selection.preset_id,
        overrides=selection.overrides.model_dump(exclude_none=True),
        selected_at=selection.selected_at,
        updated_at=selection.updated_at,
    )