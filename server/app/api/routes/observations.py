from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.observation.models import ObservationSource
from app.proactive.models import TriggerType

router = APIRouter(prefix="/observations", tags=["observations"])


class ObservationIngestRequest(BaseModel):
    source: ObservationSource
    kind: str
    payload: dict = {}
    observed_at: datetime | None = None


class ObservationIngestResponse(BaseModel):
    id: str


@router.post("/ingest", response_model=ObservationIngestResponse)
async def ingest_observation(
    payload: ObservationIngestRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> ObservationIngestResponse:
    observation = container.observation_service.ingest_event(
        current_user.id,
        "default",
        payload.source,
        payload.kind,
        payload.payload,
        payload.observed_at,
    )
    # External events are also proactive-messaging triggers.
    await container.proactive_engine.evaluate_daily(
        current_user.id,
        "default",
        TriggerType.event,
        trigger_source=payload.source.value,
    )
    return ObservationIngestResponse(id=observation.id)
