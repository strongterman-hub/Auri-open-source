from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.proactive.profile import TOTAL_ONBOARDING_SLOTS

router = APIRouter(prefix="/proactive", tags=["proactive"])


class ProactiveMessageOut(BaseModel):
    id: str
    content: str
    decided_at: str
    trigger_type: str
    actions: list[dict] = []


class ProactiveInboxResponse(BaseModel):
    messages: list[ProactiveMessageOut]


class ProactiveAckRequest(BaseModel):
    ids: list[str]


class ProactiveAckResponse(BaseModel):
    acknowledged: int


class ProactiveGuideActionRequest(BaseModel):
    type: str = Field(min_length=1, max_length=64)


class ProactiveGuideActionResponse(BaseModel):
    acknowledged: bool


class ProactiveSettingsResponse(BaseModel):
    enabled: bool


class ProactiveSettingsUpdate(BaseModel):
    enabled: bool


class ProactiveStatsResponse(BaseModel):
    phase: str
    profile_completeness: float
    total_slots: int
    completed: int
    skipped: int
    pending: int
    deferred: int
    completion_rate: float
    categories: list[dict]


@router.get("/inbox", response_model=ProactiveInboxResponse)
async def inbox(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> ProactiveInboxResponse:
    decisions = container.proactive_store.list(current_user.id, "default")
    messages: list[ProactiveMessageOut] = []
    for decision in decisions:
        if not decision.should_message:
            continue
        content = decision.message or await container.proactive_engine._decision_message(
            decision
        )
        if not content:
            continue
        messages.append(
            ProactiveMessageOut(
                id=decision.id,
                content=content,
                decided_at=decision.decided_at.isoformat(),
                trigger_type=decision.trigger_type.value,
                actions=decision.actions,
            )
        )
    return ProactiveInboxResponse(
        messages=messages
    )


@router.post("/inbox/ack", response_model=ProactiveAckResponse)
async def acknowledge_inbox(
    payload: ProactiveAckRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> ProactiveAckResponse:
    acknowledged = container.proactive_store.acknowledge(
        current_user.id,
        "default",
        payload.ids,
    )
    return ProactiveAckResponse(acknowledged=acknowledged)


@router.post("/guide-action", response_model=ProactiveGuideActionResponse)
async def guide_action_completed(
    payload: ProactiveGuideActionRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> ProactiveGuideActionResponse:
    await container.proactive_engine.complete_guide_action(
        current_user.id,
        "default",
        payload.type,
    )
    return ProactiveGuideActionResponse(acknowledged=True)


@router.get("/stats", response_model=ProactiveStatsResponse)
async def proactive_stats(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> ProactiveStatsResponse:
    return ProactiveStatsResponse(
        phase=container.profile_store.phase(current_user.id, "default"),
        profile_completeness=container.profile_store.completion_rate(
            current_user.id, "default"
        ),
        total_slots=TOTAL_ONBOARDING_SLOTS,
        completed=container.profile_store.completed_count(
            current_user.id, "default"
        ),
        skipped=container.profile_store.skipped_count(
            current_user.id, "default"
        ),
        pending=len(
            container.profile_store.pending_slots(
                current_user.id, "default"
            )
        ),
        deferred=container.profile_store.deferred_count(
            current_user.id, "default"
        ),
        completion_rate=container.profile_store.completion_rate(
            current_user.id, "default"
        ),
        categories=container.preference_store.stats(current_user.id, "default"),
    )


@router.get("/settings", response_model=ProactiveSettingsResponse)
async def get_proactive_settings(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> ProactiveSettingsResponse:
    enabled = container.proactive_settings_store.is_enabled(current_user.id, "default")
    return ProactiveSettingsResponse(enabled=enabled)


@router.put("/settings", response_model=ProactiveSettingsResponse)
async def update_proactive_settings(
    payload: ProactiveSettingsUpdate,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> ProactiveSettingsResponse:
    container.proactive_settings_store.set_enabled(
        current_user.id,
        "default",
        payload.enabled,
    )
    if payload.enabled:
        container.pacing_store.reset_control_state(
            current_user.id,
            "default",
            preference="want_more",
        )
    return ProactiveSettingsResponse(enabled=payload.enabled)
