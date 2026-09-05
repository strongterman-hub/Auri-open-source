from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.agent.context_compressor import ContextCompressor
from app.agent.runner import AgentRunContext, AgentRunner, AgentTurn
from app.agent.tools import LocationTool, Tool
from app.config import Settings
from app.files.parser import extract_text
from app.files.store import FileStore, StoredFile
from app.memory.models import MemoryScope
from app.services.memory_service import MemoryService
from app.services.event_memory_service import EventMemoryService
from app.services.presence_service import LocationRequester
from app.services.session_service import SessionService
from app.services.timezone_store import resolve_zoneinfo
from app.session.models import Session
from app.schemas.agent import FileAttachment


def _chat_message(role: str, content: str | list[dict]) -> dict:
    """Build a persistable chat message with an id and timestamp."""
    return {
        "id": uuid4().hex,
        "role": role,
        "content": content,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def _build_user_content(
    text: str,
    images: list[str] | None,
    stored_files: list[StoredFile] | None = None,
) -> str | list[dict]:
    """Build a persistable user message as plain text or a parts list."""
    images = images or []
    stored_files = stored_files or []
    if not images and not stored_files:
        return text

    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for image in images:
        parts.append({"type": "image_url", "image_url": {"url": image}})
    for file in stored_files:
        parts.append(
            {
                "type": "file",
                "file": {
                    "id": file.id,
                    "name": file.name,
                    "mime": file.mime,
                    "size": file.size,
                },
            }
        )
    return parts


@dataclass
class StreamOutcome:
    content: str = ""
    session_id: str | None = None
    reset_notice: str | None = None


class AgentService:
    """Coordinates one user turn across session, memory, and agent runtime."""

    def __init__(
        self,
        session_service: SessionService,
        memory_service: MemoryService,
        runner: AgentRunner,
        tool_factory: Callable[[MemoryScope], list[Tool]],
        compressor: ContextCompressor | None = None,
        settings: Settings | None = None,
        file_store: FileStore | None = None,
        proactive_reply_hook: Callable[
            [str, str, str, str | None], Awaitable[None]
        ] | None = None,
        proactive_activity_hook: Callable[
            [str, str, str, str | None], Awaitable[None]
        ] | None = None,
        is_onboarding: Callable[[str, str], bool] | None = None,
        next_onboarding_guide: Callable[
            [str, str], tuple[str, list[dict]] | None
        ] | None = None,
        mark_onboarding_guide_delivered: Callable[
            [str, str, str], None
        ] | None = None,
        dense_onboarding_hook: Callable[
            [str, str], Awaitable[None]
        ] | None = None,
        onboarding_context_provider: Callable[
            [str, str], str | None
        ] | None = None,
        xiaomi_status_provider: Callable[[str], dict] | None = None,
        timezone_resolver: Callable[[str], str] | None = None,
        event_memory_service: EventMemoryService | None = None,
    ) -> None:
        self.session_service = session_service
        self.memory_service = memory_service
        self.runner = runner
        self.tool_factory = tool_factory
        self.compressor = compressor
        self.settings = settings
        self.file_store = file_store
        self.proactive_reply_hook = proactive_reply_hook
        self.proactive_activity_hook = proactive_activity_hook
        self.is_onboarding = is_onboarding
        self.next_onboarding_guide = next_onboarding_guide
        self.mark_onboarding_guide_delivered = mark_onboarding_guide_delivered
        self.dense_onboarding_hook = dense_onboarding_hook
        self.onboarding_context_provider = onboarding_context_provider
        self.xiaomi_status_provider = xiaomi_status_provider
        self.timezone_resolver = timezone_resolver
        self.event_memory_service = event_memory_service
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        # SessionService owns the shared lock so chat, proactive delivery and
        # background replies cannot overwrite one another's file-backed writes.
        if hasattr(self.session_service, "lock_for"):
            return self.session_service.lock_for(session_id)
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    async def accept_message(
        self,
        session_id: str,
        client_message_id: str,
        content: str,
        images: list[str] | None = None,
        files: list[FileAttachment] | None = None,
    ) -> tuple[Session, dict, bool]:
        """Persist one user message without waiting for an Agent reply.

        The client id is the persisted idempotency key. Auxiliary event-memory
        extraction and proactive reply attribution run after the durable write so
        the Android send acknowledgement is never held open by another LLM call.
        """
        async with self._lock_for(session_id):
            current = await self.session_service.get(session_id)
            existing = next(
                (
                    message
                    for message in current.messages
                    if message.get("id") == client_message_id
                ),
                None,
            )
            if existing is not None:
                return current, existing, True

        stored_files = await self._store_files(files)
        user_content = _build_user_content(content, images, stored_files)

        async with self._lock_for(session_id):
            session = await self.session_service.get(session_id)
            existing = next(
                (
                    message
                    for message in session.messages
                    if message.get("id") == client_message_id
                ),
                None,
            )
            if existing is not None:
                return session, existing, True

            session = await self._maybe_reset(session)
            previous = session.messages[-1] if session.messages else None
            scope = MemoryScope(user_id=session.user_id, agent_id=session.agent_id)
            user_message = _chat_message("user", user_content)
            user_message["id"] = client_message_id
            proactive_id: str | None = None
            if previous and previous.get("role") == "assistant" and previous.get("proactive") is True:
                proactive_id = previous.get("id")
                user_message["replying_to_proactive_id"] = proactive_id
                if self.is_onboarding is not None:
                    user_message["onboarding_reply"] = bool(
                        self.is_onboarding(scope.user_id, scope.agent_id)
                    )
            session.messages.append(user_message)
            session.touch()
            await self.session_service.save(session)

        asyncio.create_task(
            self._after_message_accepted(scope, session.id, user_message, proactive_id)
        )
        return session, user_message, False

    async def _after_message_accepted(
        self,
        scope: MemoryScope,
        session_id: str,
        user_message: dict,
        proactive_id: str | None,
    ) -> None:
        message_text = self._content_text(user_message.get("content", ""))
        if self.proactive_activity_hook is not None:
            try:
                await self.proactive_activity_hook(
                    scope.user_id,
                    scope.agent_id,
                    message_text,
                    proactive_id,
                )
            except Exception:
                pass
        elif proactive_id and self.proactive_reply_hook is not None:
            try:
                await self.proactive_reply_hook(
                    scope.user_id,
                    scope.agent_id,
                    proactive_id,
                    message_text,
                )
            except Exception:
                pass
        if self.event_memory_service is not None:
            try:
                await self.event_memory_service.capture_user_message(
                    scope,
                    session_id,
                    user_message,
                )
            except Exception:
                pass

    async def reply_to_messages(
        self,
        session_id: str,
        message_ids: list[str],
        *,
        reply_job_id: str,
        location_requester: LocationRequester | None = None,
    ) -> tuple[AgentTurn, MemoryScope, Session, dict]:
        """Generate one complete reply for an already-persisted message batch."""
        if not message_ids:
            raise ValueError("message_ids must not be empty")

        async with self._lock_for(session_id):
            session = await self.session_service.get(session_id)
            existing_reply = next(
                (
                    message
                    for message in session.messages
                    if message.get("reply_job_id") == reply_job_id
                ),
                None,
            )
            if existing_reply is not None:
                turn = AgentTurn(text=self._content_text(existing_reply.get("content", "")))
                scope = MemoryScope(user_id=session.user_id, agent_id=session.agent_id)
                return turn, scope, session, existing_reply

            wanted = set(message_ids)
            boundary = max(
                (
                    index
                    for index, message in enumerate(session.messages)
                    if message.get("id") in wanted
                ),
                default=-1,
            )
            if boundary < 0:
                raise ValueError("reply batch messages were not found in the session")
            snapshot_session = session.model_copy(deep=True)
            snapshot_session.messages = snapshot_session.messages[: boundary + 1]

        scope = MemoryScope(
            user_id=snapshot_session.user_id,
            agent_id=snapshot_session.agent_id,
        )
        memory_snapshot = await self.memory_service.snapshot(scope)
        memory_prompt = self.memory_service.build_system_prompt(memory_snapshot)
        if self.event_memory_service is not None:
            try:
                event_context = await self.event_memory_service.build_context(scope)
                if not event_context.startswith("No structured event memory"):
                    memory_prompt = f"{memory_prompt}\n\n{event_context}"
            except Exception:
                pass

        snapshot_session, history, summary = await self._maybe_compact(
            snapshot_session, memory_prompt
        )
        llm_history: list[dict] = []
        for message in history:
            resolved = await self._resolve_llm_content(message.get("content"))
            llm_history.append({**message, "content": resolved})

        batch_messages = [
            message for message in snapshot_session.messages if message.get("id") in wanted
        ]
        onboarding_mode = bool(
            self.is_onboarding is not None
            and any(message.get("onboarding_reply") for message in batch_messages)
            and self.is_onboarding(scope.user_id, scope.agent_id)
        )
        tools = self.tool_factory(scope)
        if location_requester is not None:
            for tool in tools:
                if isinstance(tool, LocationTool):
                    tool.location_requester = location_requester

        batch_text = ", ".join(message_ids)
        context = AgentRunContext(
            session_id=snapshot_session.id,
            scope=scope,
            history=llm_history,
            memory_prompt=(
                memory_prompt
                + "\n\n[CURRENT REPLY BATCH]\n"
                + f"Reply only to the current user-message batch with ids: {batch_text}. "
                + "Older user messages marked as already settled are background context, "
                + "not unanswered requests."
            ),
            summary=summary,
            tools=tools,
            onboarding_mode=onboarding_mode,
            onboarding_actions=[],
            onboarding_context_text=self._onboarding_context_text(
                scope.user_id, scope.agent_id
            ),
            current_time=self._current_time_text(scope.user_id),
            xiaomi_status_text=self._xiaomi_status_text(scope.user_id),
        )
        turn = await self.runner.run(context)
        if not (turn.text or "").strip():
            raise ValueError("agent returned an empty reply")
        assistant_message = _chat_message("assistant", turn.text)
        assistant_message["reply_job_id"] = reply_job_id
        assistant_message["reply_to_message_ids"] = list(message_ids)

        async with self._lock_for(session_id):
            latest = await self.session_service.get(session_id)
            existing_reply = next(
                (
                    message
                    for message in latest.messages
                    if message.get("reply_job_id") == reply_job_id
                ),
                None,
            )
            if existing_reply is None:
                latest.messages.append(assistant_message)
                latest.last_prompt_tokens = int(turn.usage.get("prompt_tokens") or 0)
                if snapshot_session.summary_generation > latest.summary_generation:
                    latest.summary = snapshot_session.summary
                    latest.summary_cursor = snapshot_session.summary_cursor
                    latest.summary_generation = snapshot_session.summary_generation
                latest.touch()
                await self.session_service.save(latest)
            else:
                assistant_message = existing_reply

        return turn, scope, latest, assistant_message

    def _current_time_text(self, user_id: str) -> str:
        tz_name = "Asia/Shanghai"
        if self.timezone_resolver is not None:
            tz_name = self.timezone_resolver(user_id) or tz_name
        now = datetime.now(resolve_zoneinfo(tz_name))
        weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        return f"{now.isoformat()} ({tz_name}, {weekdays[now.weekday()]})"

    async def _prepare_turn(
        self,
        session_id: str,
        content: str | list[dict],
    ) -> tuple[Session, MemoryScope, str, list[dict], str | None]:
        session = await self.session_service.get(session_id)
        session = await self._maybe_reset(session)
        scope = MemoryScope(user_id=session.user_id, agent_id=session.agent_id)
        await self._attribute_proactive_reply(session, content)
        user_message = _chat_message("user", content)
        session.messages.append(user_message)

        if self.event_memory_service is not None:
            try:
                await self.event_memory_service.capture_user_message(
                    scope,
                    session.id,
                    user_message,
                )
            except Exception:
                # Event extraction is an auxiliary memory path and must never
                # prevent the conversational reply from completing.
                pass

        snapshot = await self.memory_service.snapshot(scope)
        memory_prompt = self.memory_service.build_system_prompt(snapshot)
        if self.event_memory_service is not None:
            try:
                event_context = await self.event_memory_service.build_context(scope)
                if not event_context.startswith("No structured event memory"):
                    memory_prompt = f"{memory_prompt}\n\n{event_context}"
            except Exception:
                pass

        session, history, summary = await self._maybe_compact(session, memory_prompt)
        llm_history: list[dict] = []
        for message in history:
            resolved = await self._resolve_llm_content(message.get("content"))
            llm_history.append({**message, "content": resolved})
        return session, scope, memory_prompt, llm_history, summary

    async def _attribute_proactive_reply(
        self,
        session: Session,
        content: str | list[dict],
    ) -> None:
        """Attribute a user reply to the preceding proactive assistant message."""
        previous = session.messages[-1] if session.messages else None
        proactive_id = None
        if previous and previous.get("role") == "assistant" and previous.get("proactive") is True:
            proactive_id = previous.get("id")
        message_text = self._content_text(content)
        if self.proactive_activity_hook is not None:
            await self.proactive_activity_hook(
                session.user_id,
                session.agent_id,
                message_text,
                proactive_id,
            )
        elif proactive_id and self.proactive_reply_hook is not None:
            await self.proactive_reply_hook(
                session.user_id,
                session.agent_id,
                proactive_id,
                message_text,
            )

    async def _is_onboarding_reply_turn(
        self,
        session: Session,
        scope: MemoryScope,
    ) -> bool:
        if self.is_onboarding is None or len(session.messages) < 2:
            return False
        previous = session.messages[-2]
        if previous.get("role") != "assistant" or previous.get("proactive") is not True:
            return False
        return bool(self.is_onboarding(scope.user_id, scope.agent_id))

    async def _onboarding_actions_for_turn(
        self,
        session: Session,
        scope: MemoryScope,
    ) -> tuple[str, list[dict]] | None:
        """Return the next setup action for a passive reply during onboarding."""
        if self.is_onboarding is None or self.next_onboarding_guide is None:
            return None
        if not self.is_onboarding(scope.user_id, scope.agent_id):
            return None
        return self.next_onboarding_guide(scope.user_id, scope.agent_id)

    def _onboarding_context_text(self, user_id: str, agent_id: str) -> str | None:
        if self.onboarding_context_provider is None:
            return None
        try:
            return self.onboarding_context_provider(user_id, agent_id)
        except Exception:
            return None

    def _xiaomi_status_text(self, user_id: str) -> str | None:
        if self.xiaomi_status_provider is None:
            return None
        try:
            status = self.xiaomi_status_provider(user_id)
        except Exception:
            return None
        if not status.get("bound"):
            return "未连接小米手环"
        last_sync = status.get("last_sync_at")
        if isinstance(last_sync, (int, float)) and last_sync:
            last_text = datetime.fromtimestamp(
                float(last_sync) / 1000.0, tz=timezone.utc
            ).isoformat()
        else:
            last_text = "未知"
        types = ", ".join(status.get("available_data_types") or [])
        return (
            "已连接小米手环；最近同步时间 "
            f"{last_text}；可用数据类型：{types or '暂无'}"
        )

    @staticmethod
    def _with_action_guidance(text: str, actions: list[dict]) -> str:
        if not actions or not text or "按钮" in text:
            return text
        return text.rstrip() + "\n\n点击下方按钮继续。"

    @staticmethod
    def _assistant_message_content(
        text: str,
        actions: list[dict],
    ) -> str | list[dict]:
        if not actions:
            return text
        text = AgentService._with_action_guidance(text, actions)
        return [
            {"type": "text", "text": text},
            {"type": "actions", "actions": actions},
        ]

    @staticmethod
    def _content_text(content: str | list[dict]) -> str:
        """Return the user-visible text from a chat message content payload."""
        if isinstance(content, str):
            return content
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "text":
                parts.append(str(part.get("text") or ""))
            elif kind == "image_url":
                parts.append("[图片]")
            elif kind == "file":
                file_info = part.get("file") or {}
                parts.append(f"[文件：{file_info.get('name', 'file')}]")
        return "\n".join(parts)

    async def _maybe_reset(self, session: Session) -> Session:
        if not self._should_reset(session):
            return session
        parent = session
        parent.end_reason = "session_reset"
        await self.session_service.save(parent)
        return await self.session_service.rotate(
            parent,
            end_reason="session_reset",
            carry_tail=False,
            reset_notice="上次会话已过期，已为你开启新会话。",
        )

    def _should_reset(self, session: Session) -> bool:
        settings = self.settings
        if settings is None:
            return False
        policy = settings.session_reset_policy
        if policy not in ("idle", "daily", "both"):
            return False

        now = datetime.now(timezone.utc)
        updated = session.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)

        idle = False
        if policy in ("idle", "both"):
            idle = (now - updated).total_seconds() > settings.session_idle_minutes * 60

        daily = False
        if policy in ("daily", "both"):
            boundary = now.replace(
                hour=settings.session_daily_at_hour,
                minute=0,
                second=0,
                microsecond=0,
            )
            if now.hour < settings.session_daily_at_hour:
                boundary -= timedelta(days=1)
            daily = updated < boundary

        return idle or daily

    async def _maybe_compact(
        self,
        session: Session,
        memory_prompt: str,
    ) -> tuple[Session, list[dict], str | None]:
        if self.compressor is None:
            return session, session.tail_messages(), session.summary

        prompt_tokens = self.compressor.estimate_prompt_tokens(session, memory_prompt)
        if not self.compressor.should_compress(prompt_tokens):
            return session, session.tail_messages(), session.summary

        result = await self.compressor.compact(session)
        if result is None:
            return session, session.tail_messages(), session.summary

        session.mark_compacted(result.summary, result.cursor, result.generation)
        return session, session.tail_messages(), session.summary

    async def _store_files(self, files: list[FileAttachment] | None) -> list[StoredFile]:
        if self.file_store is None or not files:
            return []

        stored: list[StoredFile] = []
        for file in files:
            data = base64.b64decode(file.data)
            text = await asyncio.to_thread(extract_text, file.name, file.mime, data)
            stored.append(
                await asyncio.to_thread(
                    self.file_store.save,
                    file.name,
                    file.mime,
                    data,
                    text,
                )
            )
        return stored

    async def _resolve_llm_content(self, content: str | list[dict]) -> str | list[dict]:
        if isinstance(content, str):
            return content

        parts: list[dict] = []
        for part in content:
            kind = part.get("type")
            if kind == "text":
                parts.append(part)
            elif kind == "image_url":
                parts.append(part)
            elif kind == "file":
                file_info = part.get("file") or {}
                name = file_info.get("name", "file")
                text = await self._file_text(file_info.get("id"))
                if text:
                    parts.append({"type": "text", "text": f"[文件内容：{name}]\n{text}"})
                else:
                    parts.append({"type": "text", "text": f"[用户发送了文件：{name}]"})

        if not parts:
            return ""
        if len(parts) == 1 and parts[0].get("type") == "text":
            return parts[0]["text"]
        return parts

    async def _file_text(self, file_id: str | None) -> str | None:
        if not file_id or self.file_store is None:
            return None
        stored = await asyncio.to_thread(self.file_store.get, file_id)
        return stored.text if stored else None

    async def send(
        self,
        session_id: str,
        content: str,
        images: list[str] | None = None,
        files: list[FileAttachment] | None = None,
    ) -> tuple[AgentTurn, MemoryScope, Session]:
        async with self._lock_for(session_id):
            stored_files = await self._store_files(files)
            user_content = _build_user_content(content, images, stored_files)
            session, scope, memory_prompt, history, summary = await self._prepare_turn(
                session_id, user_content
            )
            onboarding_mode = await self._is_onboarding_reply_turn(session, scope)
            onboarding_slot = None
            onboarding_actions: list[dict] = []
            onboarding_context_text = self._onboarding_context_text(
                scope.user_id, scope.agent_id
            )
            xiaomi_status_text = self._xiaomi_status_text(scope.user_id)
            current_time = self._current_time_text(scope.user_id)
            tools = self.tool_factory(scope)
            context = AgentRunContext(
                session_id=session.id,
                scope=scope,
                history=history,
                memory_prompt=memory_prompt,
                summary=summary,
                tools=tools,
                onboarding_mode=onboarding_mode,
                onboarding_actions=onboarding_actions,
                onboarding_context_text=onboarding_context_text,
                current_time=current_time,
                xiaomi_status_text=xiaomi_status_text,
            )
            turn = await self.runner.run(context)

            session.messages.append(
                _chat_message(
                    "assistant",
                    self._assistant_message_content(turn.text, onboarding_actions),
                )
            )
            session.last_prompt_tokens = int(turn.usage.get("prompt_tokens") or 0)
            session.touch()
            await self.session_service.save(session)
            if self.dense_onboarding_hook is not None:
                asyncio.create_task(
                    self.dense_onboarding_hook(scope.user_id, scope.agent_id)
                )
            return turn, scope, session

    async def send_stream(
        self,
        session_id: str,
        content: str,
        images: list[str] | None = None,
        files: list[FileAttachment] | None = None,
        location_requester: LocationRequester | None = None,
    ) -> AsyncIterator[StreamOutcome]:
        async with self._lock_for(session_id):
            stored_files = await self._store_files(files)
            user_content = _build_user_content(content, images, stored_files)
            session, scope, memory_prompt, history, summary = await self._prepare_turn(
                session_id, user_content
            )
            onboarding_mode = await self._is_onboarding_reply_turn(session, scope)
            onboarding_slot = None
            onboarding_actions: list[dict] = []
            onboarding_context_text = self._onboarding_context_text(
                scope.user_id, scope.agent_id
            )
            xiaomi_status_text = self._xiaomi_status_text(scope.user_id)
            current_time = self._current_time_text(scope.user_id)
            # Persist the user message plus a "（已停止）" placeholder immediately.
            # On normal completion the placeholder is replaced with the full text;
            # if the client stops, the placeholder stays, so a stopped reply is still
            # visible after reloading instead of disappearing.
            session.messages.append(_chat_message("assistant", "（已停止）"))
            session.touch()
            await self.session_service.save(session)
            placeholder_index = len(session.messages) - 1

            tools = self.tool_factory(scope)
            if location_requester is not None:
                for tool in tools:
                    if isinstance(tool, LocationTool):
                        tool.location_requester = location_requester
            context = AgentRunContext(
                session_id=session.id,
                scope=scope,
                history=history,
                memory_prompt=memory_prompt,
                summary=summary,
                tools=tools,
                onboarding_mode=onboarding_mode,
                onboarding_actions=onboarding_actions,
                onboarding_context_text=onboarding_context_text,
                current_time=current_time,
                xiaomi_status_text=xiaomi_status_text,
            )

            turn: AgentTurn | None = None
            async for event in self.runner.run_stream(context):
                if event.content:
                    yield StreamOutcome(content=event.content)
                if event.done:
                    turn = event.turn

            if turn is not None:
                session.messages[placeholder_index]["content"] = (
                    self._assistant_message_content(
                        turn.text or "（无回复）",
                        onboarding_actions,
                    )
                )
                session.last_prompt_tokens = int(turn.usage.get("prompt_tokens") or 0)
                session.touch()
                await self.session_service.save(session)
                if self.dense_onboarding_hook is not None:
                    asyncio.create_task(
                        self.dense_onboarding_hook(scope.user_id, scope.agent_id)
                    )

            yield StreamOutcome(session_id=session.id, reset_notice=session.reset_notice)
