from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.memory.models import MemoryScope
from app.schemas.persona import (
    PersonaCharacterSummary,
    PersonaMeResponse,
    PersonaPresetSummary,
    PersonaPresetsResponse,
    PersonaSelectionRequest,
    PersonaSelectionResponse,
    PersonaSelectionSummary,
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
    service = container.persona_service
    user_selection_enabled = bool(container.settings.persona_user_selection_enabled)
    available = service.is_enabled(scope)
    portrait_available = False
    portrait_service = getattr(container, "portrait_service", None)
    if portrait_service is not None:
        try:
            portrait_available = bool(portrait_service.feature_available(scope))
        except Exception:
            portrait_available = False
    selection = service.effective_user_selection(scope)
    source = (
        "user"
        if user_selection_enabled and service.has_user_selection(scope)
        else "default"
    )
    presets = [
        PersonaPresetSummary(
            id=item.get("id", ""),
            label=item.get("label") or item.get("id", ""),
            description=item.get("description", ""),
            proactive_frequency_preset=item.get("proactive_frequency_preset", "high"),
        )
        for item in service.list_presets()
    ]
    characters: list[PersonaCharacterSummary] = []
    if portrait_available:
        characters = [
            PersonaCharacterSummary(
                presentation=item["presentation"],
                label=item.get("label") or item["presentation"],
                display_name=item.get("display_name") or "Auri",
                summary=item.get("summary") or "",
                avatar_url=(
                    portrait_service.avatar_url(item["presentation"])
                    if portrait_service is not None
                    else ""
                ),
            )
            for item in service.list_characters()
        ]
    return PersonaMeResponse(
        preset=service.resolve_preset(scope).model_dump(),
        relationship=service.relationship_state(scope).model_dump(),
        user_selection_enabled=user_selection_enabled,
        available=available,
        portrait_available=portrait_available,
        selection=PersonaSelectionSummary(
            preset_id=selection.preset_id,
            presentation=selection.overrides.presentation
            or service.default_presentation(),
            source=source,
        ),
        presets=presets,
        characters=characters,
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
        selection = container.persona_service.update_user_preferences(
            scope,
            preset_id=payload.preset_id,
            presentation=payload.presentation,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return PersonaSelectionResponse(
        selection=PersonaSelectionSummary(
            preset_id=selection.preset_id,
            presentation=selection.overrides.presentation
            or container.persona_service.default_presentation(),
            source="user",
        ),
        updated_at=selection.updated_at,
    )