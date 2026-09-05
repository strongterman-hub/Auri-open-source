from __future__ import annotations

import base64
import hashlib
import hmac
import time


def _sign(message: str, key: str) -> str:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def create_session_token(username: str, password: str, ttl_seconds: int) -> str:
    """Build an HMAC-signed, expiring session token (no extra dependencies)."""
    expires = int(time.time()) + ttl_seconds
    payload = f"{username}.{expires}"
    signature = _sign(payload, password)
    raw = f"{payload}.{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def verify_session_token(token: str, password: str) -> str | None:
    """Return the username if the token is valid and unexpired, else None."""
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        username, expires_str, signature = raw.rsplit(".", 2)
        expires = int(expires_str)
    except (ValueError, UnicodeDecodeError):
        return None
    if time.time() > expires:
        return None
    expected = _sign(f"{username}.{expires}", password)
    if not hmac.compare_digest(expected, signature):
        return None
    return username
