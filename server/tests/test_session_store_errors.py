import asyncio
from pathlib import Path

import pytest

from app.core.errors import ServiceUnavailableError
from app.services.session_service import SessionService
from app.session.models import SessionCreate
from app.session.store import FileSessionStore


def test_only_missing_session_is_not_found(tmp_dir):
    store = FileSessionStore(tmp_dir)
    assert asyncio.run(store.get("missing")) is None


@pytest.mark.parametrize("failure", [PermissionError("denied"), OSError("disk read failed")])
def test_read_failure_is_not_reported_as_missing(tmp_dir, monkeypatch, failure, caplog):
    store = FileSessionStore(tmp_dir)
    session = asyncio.run(store.create(SessionCreate(user_id="u")))
    def fail_read(*args, **kwargs):
        raise failure
    monkeypatch.setattr(Path, "read_text", fail_read)
    with pytest.raises(ServiceUnavailableError) as raised:
        asyncio.run(SessionService(store).get(session.id))
    assert raised.value.status_code == 503
    assert "session_read_failed" in caplog.text
    assert "denied" not in str(raised.value)


def test_corrupt_session_is_not_reported_as_missing(tmp_dir):
    (tmp_dir / "broken.json").write_text('{"messages":', encoding="utf-8")
    with pytest.raises(ServiceUnavailableError):
        asyncio.run(FileSessionStore(tmp_dir).get("broken"))
