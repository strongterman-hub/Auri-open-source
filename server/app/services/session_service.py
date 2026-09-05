from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.core.errors import NotFoundError
from app.session.models import Session, SessionCreate
from app.session.store import SessionStore


class SessionService:
    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, session_id: str) -> asyncio.Lock:
        """Return the shared write lock for every producer of one chat session."""
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    async def create(self, payload: SessionCreate) -> Session:
        return await self.store.create(payload)

    async def get(self, session_id: str) -> Session:
        session = await self.store.get(session_id)
        if session is None:
            raise NotFoundError(message=f"Session '{session_id}' was not found.")
        if session.normalize_message_ids():
            await self.store.save(session)
        return session

    async def save(self, session: Session) -> Session:
        return await self.store.save(session)

    async def append_message(self, session_id: str, message: dict) -> Session:
        """Reload and append under the shared session lock to prevent lost writes."""
        async with self.lock_for(session_id):
            session = await self.get(session_id)
            message_id = message.get("id")
            if message_id and any(item.get("id") == message_id for item in session.messages):
                return session
            session.messages.append(message)
            session.touch()
            return await self.save(session)

    async def message_by_id(self, session_id: str, message_id: str) -> dict | None:
        session = await self.get(session_id)
        return next(
            (message for message in session.messages if message.get("id") == message_id),
            None,
        )

    async def messages_after(
        self,
        session_id: str,
        after_id: str | None,
        *,
        limit: int = 100,
    ) -> list[dict]:
        session = await self.get(session_id)
        if not after_id:
            return session.messages[-limit:]
        index = next(
            (
                index
                for index, message in enumerate(session.messages)
                if message.get("id") == after_id
            ),
            None,
        )
        if index is None:
            return session.messages[-limit:]
        return session.messages[index + 1 : index + 1 + limit]

    async def settle_messages(
        self,
        session_id: str,
        message_ids: list[str],
        *,
        outcome: str,
        reply_job_id: str,
    ) -> Session:
        wanted = set(message_ids)
        async with self.lock_for(session_id):
            session = await self.get(session_id)
            changed = False
            for message in session.messages:
                if message.get("id") in wanted:
                    message["reply_disposition"] = outcome
                    message["reply_job_id"] = reply_job_id
                    changed = True
            if changed:
                session.touch()
                await self.save(session)
            return session

    async def list_by_user(self, user_id: str) -> list[Session]:
        sessions = await self.store.list()
        return [session for session in sessions if session.user_id == user_id]

    async def delete_by_user(self, user_id: str) -> None:
        for session in await self.list_by_user(user_id):
            await self.store.delete(session.id)

    async def list_users(self) -> list[tuple[str, str]]:
        """Return unique (user_id, agent_id) pairs with at least one session."""
        sessions = await self.store.list()
        seen: set[tuple[str, str]] = set()
        result: list[tuple[str, str]] = []
        for session in sessions:
            key = (session.user_id, session.agent_id)
            if key not in seen:
                seen.add(key)
                result.append(key)
        return result

    async def has_user_history(self, user_id: str) -> bool:
        """Return whether the user has sent at least one chat message."""
        for session in await self.list_by_user(user_id):
            for message in session.messages:
                if message.get("role") == "user":
                    return True
        return False

    async def ensure(self, payload: SessionCreate) -> Session:
        """Return the user's latest active session, creating one if none exists."""
        active = [s for s in await self.list_by_user(payload.user_id) if s.end_reason is None]
        if active:
            return max(active, key=lambda s: s.updated_at)
        return await self.store.create(payload)

    async def rotate(
        self,
        parent: Session,
        *,
        end_reason: str,
        carry_tail: bool,
        reset_notice: str | None = None,
    ) -> Session:
        """Close ``parent`` by creating a linked continuation session."""
        now = datetime.now(timezone.utc)
        messages = list(parent.messages[parent.summary_cursor :]) if carry_tail else []
        child = Session(
            id=uuid4().hex,
            user_id=parent.user_id,
            agent_id=parent.agent_id,
            messages=messages,
            summary=parent.summary,
            summary_cursor=0,
            summary_generation=parent.summary_generation,
            parent_session_id=parent.id,
            end_reason=None,
            reset_notice=reset_notice,
            metadata=dict(parent.metadata),
            created_at=now,
            updated_at=now,
        )
        return await self.store.save(child)
