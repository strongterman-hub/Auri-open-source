from fastapi import APIRouter, status

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.core.errors import NotFoundError
from app.session.models import Session, SessionCreate

router = APIRouter()


@router.post("", response_model=Session, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreate,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> Session:
    scoped_payload = SessionCreate(
        user_id=current_user.id,
        agent_id=payload.agent_id,
        metadata=payload.metadata,
    )
    return await container.session_service.create(scoped_payload)


@router.post("/ensure", response_model=Session)
async def ensure_session(
    payload: SessionCreate,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> Session:
    scoped_payload = SessionCreate(
        user_id=current_user.id,
        agent_id=payload.agent_id,
        metadata=payload.metadata,
    )
    return await container.session_service.ensure(scoped_payload)


@router.get("/{session_id}", response_model=Session)
async def get_session(
    session_id: str,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> Session:
    session = await container.session_service.get(session_id)
    if session.user_id != current_user.id:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")
    return session


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> None:
    session = await container.session_service.get(session_id)
    if session.user_id != current_user.id:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")
    container.chat_reply_store.cancel_session(session_id)
    deleted = await container.session_store.delete(session_id)
    if not deleted:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")
