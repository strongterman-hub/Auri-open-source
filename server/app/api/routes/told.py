from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.memory.told import ToldFeedback

router = APIRouter(prefix="/told", tags=["told"])


class ToldFeedbackRequest(BaseModel):
    feedback: ToldFeedback


class ToldFeedbackResponse(BaseModel):
    updated: bool


@router.post("/{insight_key}/feedback", response_model=ToldFeedbackResponse)
async def update_told_feedback(
    insight_key: str,
    payload: ToldFeedbackRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> ToldFeedbackResponse:
    updated = container.told_store.update_feedback(
        insight_key,
        current_user.id,
        "default",
        payload.feedback,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="该消息不存在或已过期")
    return ToldFeedbackResponse(updated=updated)
