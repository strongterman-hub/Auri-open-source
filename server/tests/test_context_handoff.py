import json
import asyncio
import os
import stat

import pytest

from app.session.models import Session, SessionCreate
from deploy.rebuild_chat_context import apply, fingerprint, read_session


def candidate(tmp_dir):
    session = Session.create(SessionCreate(user_id="u"), "s")
    session.messages = [{"id": str(i), "role": "user" if i % 2 == 0 else "assistant",
                         "content": str(i)} for i in range(10)]
    path = tmp_dir / "sessions" / "s.json"
    path.parent.mkdir()
    path.write_text(session.model_dump_json(), encoding="utf-8")
    handoff = {"session_id": "s", "user_id": "u", "summary": "confirmed facts", "cursor": 6,
               "generation": 1, "prefix_hash": fingerprint(session.messages[:6]),
               "previous_summary_hash": fingerprint([None, 0, 0])}
    cp = tmp_dir / "candidate.json"
    cp.write_text(json.dumps(handoff), encoding="utf-8")
    return session, path, cp


def test_apply_keeps_archive_appended_messages_and_memory(tmp_dir):
    session, path, cp = candidate(tmp_dir)
    memory = tmp_dir / "memory.json"
    memory.write_text('["remember me"]')
    session.messages.append({"id": "new", "role": "user", "content": "new message"})
    path.write_text(session.model_dump_json(), encoding="utf-8")
    before = path.read_bytes()
    result = apply(tmp_dir, cp, tmp_dir / "backup")
    saved = read_session(tmp_dir, "s")
    assert saved.messages == session.messages and saved.summary_cursor == 6
    assert saved.summary == "confirmed facts" and result["transcript_unchanged"]
    assert (tmp_dir / "backup" / "s.json").read_bytes() == before
    assert memory.read_text() == '["remember me"]'


def test_apply_preserves_access_mode(tmp_dir):
    _session, path, cp = candidate(tmp_dir)
    os.chmod(path, 0o640)
    original = path.stat()
    apply(tmp_dir, cp, tmp_dir / "backup")
    current = path.stat()
    assert stat.S_IMODE(current.st_mode) == stat.S_IMODE(original.st_mode)
    assert (current.st_uid, current.st_gid) == (original.st_uid, original.st_gid)


@pytest.mark.skipif(os.name != "posix" or not hasattr(os, "geteuid") or os.geteuid() != 0,
                    reason="requires POSIX root to simulate root maintenance of a service-owned file")
def test_root_migration_preserves_service_owner(tmp_dir):
    import pwd
    _session, path, cp = candidate(tmp_dir)
    service_user = pwd.getpwnam("nobody")
    os.chown(path, service_user.pw_uid, service_user.pw_gid)
    os.chmod(path, 0o600)
    apply(tmp_dir, cp, tmp_dir / "backup")
    current = path.stat()
    assert (current.st_uid, current.st_gid) == (service_user.pw_uid, service_user.pw_gid)
    assert stat.S_IMODE(current.st_mode) == 0o600


@pytest.mark.parametrize("changed", ["history", "summary"])
def test_apply_refuses_stale_handoff(tmp_dir, changed):
    session, path, cp = candidate(tmp_dir)
    if changed == "history":
        session.messages[0]["content"] = "changed"
    else:
        session.summary = "new summary"
    path.write_text(session.model_dump_json(), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="changed_since_preparation"):
        apply(tmp_dir, cp, tmp_dir / "backup")
    assert path.read_bytes() == before


def test_preparation_resumes_checkpoint_without_touching_live_session(tmp_dir, monkeypatch):
    from app.agent.llm import LLMResponse
    from app.config import Settings
    import deploy.rebuild_chat_context as maintenance
    session, path, cp = candidate(tmp_dir)
    cp.unlink()
    for message in session.messages:
        message["content"] = "word" * 40
    path.write_text(session.model_dump_json(), encoding="utf-8")
    original = path.read_bytes()
    class LLM:
        calls = 0
        fail = True
        async def complete_structured(self, *args, **kwargs):
            self.calls += 1
            if self.fail and cp.with_suffix(".checkpoint.json").exists():
                return LLMResponse(content="truncated", finish_reason="length")
            return LLMResponse(content="confirmed facts")
    llm = LLM()
    monkeypatch.setattr(maintenance, "build_llm_client", lambda *args, **kwargs: llm)
    monkeypatch.setattr(maintenance, "Settings", lambda: Settings(_env_file=None, compression_chunk_tokens=100))
    with pytest.raises(ValueError, match="handoff_incomplete"):
        asyncio.run(maintenance.prepare(tmp_dir, "s", cp, 4))
    checkpoint = json.loads(cp.with_suffix(".checkpoint.json").read_text())
    assert 0 < checkpoint["cursor"] < 6 and path.read_bytes() == original
    llm.fail = False
    asyncio.run(maintenance.prepare(tmp_dir, "s", cp, 4))
    assert path.read_bytes() == original
    apply(tmp_dir, cp, tmp_dir / "backup")
    assert read_session(tmp_dir, "s").summary_generation == 1
