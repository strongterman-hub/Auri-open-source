from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def client(tmp_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AURI_DATA_DIR", str(tmp_dir))
    monkeypatch.setenv("AURI_LLM_PROVIDER", "echo")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def _register(client, email: str) -> dict[str, str]:
    response = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "test1234"},
    )
    assert response.status_code == 201
    token = response.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_profile_timezone_roundtrip(client) -> None:
    headers = _register(client, "tz1@example.com")

    default = client.get("/v1/profile/timezone", headers=headers)
    assert default.status_code == 200
    assert default.json()["timezone"] == "Asia/Shanghai"

    updated = client.put(
        "/v1/profile/timezone",
        json={"timezone": "America/New_York"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["timezone"] == "America/New_York"

    fetched = client.get("/v1/profile/timezone", headers=headers)
    assert fetched.json()["timezone"] == "America/New_York"


def test_heartbeat_reports_timezone(client) -> None:
    headers = _register(client, "tz2@example.com")

    heartbeat = client.post(
        "/v1/presence/heartbeat",
        json={"timezone": "Europe/London"},
        headers=headers,
    )
    assert heartbeat.status_code == 200

    fetched = client.get("/v1/profile/timezone", headers=headers)
    assert fetched.json()["timezone"] == "Europe/London"
