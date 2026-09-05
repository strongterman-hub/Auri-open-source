from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.session.models import Session, SessionCreate


class SessionStore(ABC):
    @abstractmethod
    async def create(self, payload: SessionCreate) -> Session:
        """Create and persist a new session."""

    @abstractmethod
    async def get(self, session_id: str) -> Session | None:
        """Load a session by id."""

    @abstractmethod
    async def save(self, session: Session) -> Session:
        """Persist an existing session."""

    @abstractmethod
    async def delete(self, session_id: str) -> bool:
        """Delete a session by id."""

    @abstractmethod
    async def list(self) -> list[Session]:
        """List all persisted sessions."""


class FileSessionStore(SessionStore):
    """JSON-file-backed session store for development or a single-node server."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.json"

    def _read(self, session_id: str) -> Session | None:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return Session.model_validate(payload)

    def _write(self, session: Session) -> Session:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = session.model_dump(mode="json")
        tmp_path = self._path(session.id).with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self._path(session.id))
        return session

    async def create(self, payload: SessionCreate) -> Session:
        session_id = uuid4().hex
        session = Session.create(payload, session_id)
        return await asyncio.to_thread(self._write, session)

    async def get(self, session_id: str) -> Session | None:
        return await asyncio.to_thread(self._read, session_id)

    async def save(self, session: Session) -> Session:
        return await asyncio.to_thread(self._write, session)

    async def delete(self, session_id: str) -> bool:
        path = self._path(session_id)
        if not path.exists():
            return False

        def _remove() -> None:
            path.unlink()

        await asyncio.to_thread(_remove)
        return True

    async def list(self) -> list[Session]:
        def _list() -> list[Session]:
            sessions: list[Session] = []
            for path in self.root.glob("*.json"):
                session = self._read(path.stem)
                if session is not None:
                    sessions.append(session)
            return sessions

        return await asyncio.to_thread(_list)
