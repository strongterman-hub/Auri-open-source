from fastapi import APIRouter

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.core.errors import NotFoundError
from app.memory.models import MemoryScope
from app.schemas.memory import (
    MemoryConsolidationResponse,
    MemorySnapshotResponse,
    MemoryWriteRequest,
    MemoryWriteResponse,
)

router = APIRouter()


@router.get("/{session_id}/memory", response_model=MemorySnapshotResponse)
async def get_memory(
    session_id: str,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> MemorySnapshotResponse:
    session = await container.session_service.get(session_id)
    if session.user_id != current_user.id:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")
    scope = MemoryScope(user_id=session.user_id, agent_id=session.agent_id)
    snapshot = await container.memory_service.snapshot(scope)
    return MemorySnapshotResponse(snapshot=snapshot)


@router.post("/{session_id}/memory", response_model=MemoryWriteResponse)
async def write_memory(
    session_id: str,
    payload: MemoryWriteRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> MemoryWriteResponse:
    session = await container.session_service.get(session_id)
    if session.user_id != current_user.id:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")
    scope = MemoryScope(user_id=session.user_id, agent_id=session.agent_id)
    result = await container.memory_service.write(scope, payload.operations)
    snapshot = await container.memory_service.snapshot(scope)
    return MemoryWriteResponse(
        success=result.success,
        error=result.error,
        memory_usage=snapshot.memory_usage,
        user_usage=snapshot.user_usage,
        memory=snapshot.memory,
        user=snapshot.user,
    )


@router.post("/{session_id}/memory/consolidate", response_model=MemoryConsolidationResponse)
async def consolidate_memory(
    session_id: str,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> MemoryConsolidationResponse:
    session = await container.session_service.get(session_id)
    if session.user_id != current_user.id:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")
    scope = MemoryScope(user_id=session.user_id, agent_id=session.agent_id)
    result = await container.consolidator.consolidate(scope)
    return MemoryConsolidationResponse(
        promoted=result.promoted,
        candidates=result.candidates,
        error=result.error,
    )
