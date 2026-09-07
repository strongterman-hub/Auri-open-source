from __future__ import annotations

import time
from pathlib import Path

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


def test_admin_pages_include_operational_navigation() -> None:
    static_dir = Path(__file__).resolve().parents[1] / "app" / "static"
    admin = (static_dir / "admin.html").read_text(encoding="utf-8")
    login = (static_dir / "login.html").read_text(encoding="utf-8")

    assert "Auri 运营中心" in admin
    assert "用户与 Credits" in admin
    assert "对话与工具" in admin
    assert "版本发布" in admin
    assert "Asia/Shanghai" in admin
    assert "mobile-cards" in admin
    assert '/static/site/assets/auri-icon.png' in admin
    assert '/static/site/assets/auri-icon.png' in login
    assert "noindex,nofollow" in admin
    assert "autocomplete=\"current-password\"" in login
    assert "Caps Lock 已开启" in login
