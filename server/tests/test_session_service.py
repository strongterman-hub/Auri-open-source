from datetime import datetime, timezone
from uuid import uuid4

from app.services.session_service import SessionService
from app.session.models import Session, SessionCreate
from app.session.store import SessionStore


class InMemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def create(self, payload: SessionCreate) -> Session:
        session = Session.create(payload, uuid4().hex)
        self.sessions[session.id] = session
        return session

    async def get(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def save(self, session: Session) -> Session:
        self.sessions[session.id] = session
        return session

    async def delete(self, session_id: str) -> bool:
        return self.sessions.pop(session_id, None) is not None

    async def list(self) -> list[Session]:
        return list(self.sessions.values())


def _payload(user_id: str = "u1") -> SessionCreate:
    return SessionCreate(user_id=user_id, agent_id="default")


def test_ensure_returns_latest_active_session() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)
    older = asyncio_run(service.create(_payload()))
    older.touch(datetime(2026, 1, 1, tzinfo=timezone.utc))
    asyncio_run(service.save(older))
    newer = asyncio_run(service.create(_payload()))
    newer.touch(datetime(2026, 1, 2, tzinfo=timezone.utc))
    asyncio_run(service.save(newer))

    ensured = asyncio_run(service.ensure(_payload()))

    assert ensured.id == newer.id


def test_ensure_ignores_ended_sessions_and_creates() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)
    ended = asyncio_run(service.create(_payload()))
    ended.end_reason = "compression"
    asyncio_run(service.save(ended))

    ensured = asyncio_run(service.ensure(_payload()))

    assert ensured.id != ended.id
    assert ensured.end_reason is None


def test_rotate_carries_tail_and_links_parent() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)
    parent = asyncio_run(service.create(_payload()))
    parent.messages = [
        {"id": "1", "role": "user", "content": "a"},
        {"id": "2", "role": "assistant", "content": "b"},
        {"id": "3", "role": "user", "content": "c"},
    ]
    parent.summary = "earlier"
    parent.summary_cursor = 2
    asyncio_run(service.save(parent))

    child = asyncio_run(service.rotate(parent, end_reason="compression", carry_tail=True))

    assert child.parent_session_id == parent.id
    assert child.summary == "earlier"
    assert child.summary_cursor == 0
    assert [m["id"] for m in child.messages] == ["3"]


def test_rotate_reset_clears_messages_and_sets_notice() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)
    parent = asyncio_run(service.create(_payload()))
    parent.messages = [{"id": "1", "role": "user", "content": "a"}]
    asyncio_run(service.save(parent))

    child = asyncio_run(
        service.rotate(
            parent,
            end_reason="session_reset",
            carry_tail=False,
            reset_notice="expired",
        )
    )

    assert child.messages == []
    assert child.reset_notice == "expired"


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
