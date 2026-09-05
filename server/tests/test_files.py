from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from app.files.parser import extract_text
from app.files.store import FileStore
from app.schemas.agent import SendMessageRequest
from app.services.agent_service import AgentService, _build_user_content


def test_file_store_round_trip_with_text(tmp_dir: Path) -> None:
    store = FileStore(tmp_dir / "files")
    stored = store.save("notes.txt", "text/plain", b"hello world", text="hello world")

    assert store.read(stored.id) == b"hello world"
    got = store.get(stored.id)
    assert got is not None
    assert got.text == "hello world"


def test_build_user_content_includes_file_parts(tmp_dir: Path) -> None:
    store = FileStore(tmp_dir / "files")
    stored = store.save("notes.txt", "text/plain", b"hello")

    content = _build_user_content("see attached", [], [stored])

    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "see attached"}
    assert content[1]["type"] == "file"
    assert content[1]["file"]["id"] == stored.id


def test_extract_text_from_plain_text() -> None:
    assert extract_text("notes.txt", "text/plain", "你好 world".encode("utf-8")) == "你好 world"


def test_extract_text_returns_none_for_unknown_binary() -> None:
    assert extract_text("blob.bin", "application/octet-stream", b"\x00\x01\x02") is None


def _service_with_file_store(store: FileStore) -> AgentService:
    service = AgentService.__new__(AgentService)
    service.file_store = store
    return service


def test_resolve_llm_content_injects_extracted_text(tmp_dir: Path) -> None:
    store = FileStore(tmp_dir / "files")
    stored = store.save("notes.txt", "text/plain", b"hello", text="hello content")
    service = _service_with_file_store(store)

    resolved = asyncio.run(
        service._resolve_llm_content(
            [{"type": "file", "file": {"id": stored.id, "name": "notes.txt"}}],
        )
    )

    assert resolved == "[文件内容：notes.txt]\nhello content"


def test_resolve_llm_content_falls_back_to_file_name(tmp_dir: Path) -> None:
    store = FileStore(tmp_dir / "files")
    stored = store.save("blob.bin", "application/octet-stream", b"\x00", text=None)
    service = _service_with_file_store(store)

    resolved = asyncio.run(
        service._resolve_llm_content(
            [{"type": "file", "file": {"id": stored.id, "name": "blob.bin"}}],
        )
    )

    assert resolved == "[用户发送了文件：blob.bin]"


def test_send_message_request_accepts_files() -> None:
    data = base64.b64encode(b"hello").decode()
    request = SendMessageRequest(
        files=[{"name": "notes.txt", "mime": "text/plain", "data": data}],
    )
    assert request.content == ""
    assert len(request.files) == 1
    assert request.files[0].name == "notes.txt"
