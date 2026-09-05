import asyncio
import json
import logging

from fastapi import APIRouter, Query, status
from fastapi.responses import StreamingResponse

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.core.errors import NotFoundError
from app.schemas.agent import (
    AsyncSendMessageRequest,
    AsyncSendMessageResponse,
    ChatUpdatesResponse,
    MessagePage,
    SendMessageRequest,
    SendMessageResponse,
)
from app.services.agent_service import StreamOutcome
from app.services.presence_service import LocationRequester
from app.session.messages import paginate_messages

router = APIRouter()
logger = logging.getLogger("auri.agent")


@router.get("/{session_id}/messages", response_model=MessagePage)
async def list_messages(
    session_id: str,
    container: ContainerDep,
    current_user: CurrentUserDep,
    before: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> MessagePage:
    session = await container.session_service.get(session_id)
    if session.user_id != current_user.id:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")

    page, has_more = paginate_messages(session.messages, before, limit)
    return MessagePage(messages=page, has_more=has_more)


@router.post("/{session_id}/messages", response_model=SendMessageResponse)
async def send_message(
    session_id: str,
    payload: SendMessageRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> SendMessageResponse:
    session = await container.session_service.get(session_id)
    if session.user_id != current_user.id:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")
    container.billing_service.ensure_available(current_user.id)
    turn, _scope, session = await container.agent_service.send(
        session_id,
        payload.content,
        payload.images,
        payload.files,
    )
    return SendMessageResponse(
        session_id=session.id,
        message={"role": "assistant", "content": turn.text},
        usage=turn.usage,
        tool_results=turn.tool_results,
        reset_notice=session.reset_notice,
    )


@router.post(
    "/{session_id}/messages/async",
    response_model=AsyncSendMessageResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def send_message_async(
    session_id: str,
    payload: AsyncSendMessageRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> AsyncSendMessageResponse:
    session = await container.session_service.get(session_id)
    if session.user_id != current_user.id:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")
    container.billing_service.ensure_available(current_user.id)
    accepted_session, message, reply_state = await container.chat_reply_service.accept(
        session_id,
        payload.client_message_id,
        payload.content,
        payload.images,
        payload.files,
    )
    return AsyncSendMessageResponse(
        session_id=accepted_session.id,
        message=message,
        reply_state=reply_state,
        reset_notice=accepted_session.reset_notice,
    )


@router.get("/{session_id}/updates", response_model=ChatUpdatesResponse)
async def chat_updates(
    session_id: str,
    container: ContainerDep,
    current_user: CurrentUserDep,
    after: str | None = Query(default=None),
) -> ChatUpdatesResponse:
    session = await container.session_service.get(session_id)
    if session.user_id != current_user.id:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")
    messages = await container.session_service.messages_after(session_id, after)
    reply_state, commands = container.chat_reply_service.updates(session_id)
    next_poll_ms = 1_000 if reply_state == "typing" else (
        2_000 if reply_state == "queued" else 10_000
    )
    return ChatUpdatesResponse(
        session_id=session_id,
        messages=messages,
        reply_state=reply_state,
        commands=commands,
        next_poll_ms=next_poll_ms,
    )


@router.post("/{session_id}/messages/stream")
async def send_message_stream(
    session_id: str,
    payload: SendMessageRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> StreamingResponse:
    session = await container.session_service.get(session_id)
    if session.user_id != current_user.id:
        raise NotFoundError(message=f"Session '{session_id}' was not found.")
    container.billing_service.ensure_available(current_user.id)

    queue: asyncio.Queue = asyncio.Queue()

    async def emit_location_request(request_id: str) -> None:
        await queue.put({"request_location": True, "request_id": request_id})

    location_requester = LocationRequester(
        container.presence_service,
        emit_location_request,
    )

    async def produce() -> None:
        try:
            async for event in container.agent_service.send_stream(
                session_id,
                payload.content,
                payload.images,
                payload.files,
                location_requester=location_requester,
            ):
                await queue.put(event)
        finally:
            await queue.put(None)

    async def event_stream():
        producer = asyncio.create_task(produce())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                if isinstance(item, dict) and item.get("request_location"):
                    yield (
                        "data: "
                        + json.dumps(
                            {
                                "request_location": True,
                                "request_id": item.get("request_id"),
                            },
                            ensure_ascii=False,
                        )
                        + "\n\n"
                    )
                elif isinstance(item, StreamOutcome):
                    if item.content:
                        yield f"data: {json.dumps({'content': item.content}, ensure_ascii=False)}\n\n"
                    else:
                        yield f"data: {json.dumps({'session_id': item.session_id, 'reset_notice': item.reset_notice}, ensure_ascii=False)}\n\n"
        finally:
            producer.cancel()
            try:
                await producer
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("agent stream failed")
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
