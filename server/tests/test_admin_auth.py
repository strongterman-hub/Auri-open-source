from __future__ import annotations

import time

from app.core.admin_auth import create_session_token, verify_session_token


def test_session_token_roundtrip() -> None:
    token = create_session_token("admin", "secret", ttl_seconds=3600)
    assert verify_session_token(token, "secret") == "admin"


def test_session_token_rejects_wrong_password() -> None:
    token = create_session_token("admin", "secret", ttl_seconds=3600)
    assert verify_session_token(token, "wrong") is None


def test_session_token_rejects_expired() -> None:
    token = create_session_token("admin", "secret", ttl_seconds=-1)
    assert verify_session_token(token, "secret") is None
