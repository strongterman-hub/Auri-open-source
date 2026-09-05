import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.auth.email_sender import EmailSender
from app.auth.verification import EmailVerificationService, VerificationCodeStore
from app.memory.events import EventCandidate, EventStatus, TimePrecision
from app.memory.models import OriginClass


@pytest.fixture
def client(tmp_dir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AURI_DATA_DIR", str(tmp_dir))
    monkeypatch.setenv("AURI_LLM_PROVIDER", "echo")
    # Keep proactive-messaging tests deterministic regardless of the wall clock.
    monkeypatch.setenv("AURI_PROACTIVE_QUIET_HOURS", "")

    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_health(client) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_public_root_does_not_expose_debug_console(client) -> None:
    root = client.get("/")
    debug = client.get("/debug")

    assert root.status_code == 200
    assert 'id="hero-title"' in root.text
    assert 'href="/download"' in root.text
    assert "frame-ancestors 'none'" in root.headers["content-security-policy"]
    assert debug.status_code == 404


def test_public_download_page_uses_update_api(client) -> None:
    page = client.get("/download")
    script = client.get("/static/site/site.js")
    icon = client.get("/static/site/assets/auri-icon.png")

    assert page.status_code == 200
    assert "下载 Auri" in page.text
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert 'fetch("/v1/update/check"' in script.text
    assert 'return `/v1${value}`' in script.text
    assert icon.status_code == 200
    assert icon.headers["content-type"] == "image/png"


def test_session_message_round_trip(client) -> None:
    register_response = client.post(
        "/v1/auth/register",
        json={"email": "bob@example.com", "password": "test1234"},
    )
    assert register_response.status_code == 201
    token = register_response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    session_response = client.post("/v1/sessions", json={"user_id": "alice"}, headers=headers)
    assert session_response.status_code == 201
    session_id = session_response.json()["id"]

    message_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"content": "hello"},
        headers=headers,
    )
    assert message_response.status_code == 200
    assert message_response.json()["message"]["content"] == "echo: hello"


def test_async_message_is_acknowledged_then_replied_and_is_idempotent(client) -> None:
    register = client.post(
        "/v1/auth/register",
        json={"email": "async-chat@example.com", "password": "test1234"},
    )
    headers = {"Authorization": f"Bearer {register.json()['token']}"}
    session = client.post("/v1/sessions", json={"user_id": "ignored"}, headers=headers)
    session_id = session.json()["id"]
    service = client.app.state.container.chat_reply_service
    service.debounce_seconds = 0
    service.delays["fast"] = (0, 0)

    payload = {"client_message_id": "client-message-1", "content": "你在吗？"}
    accepted = client.post(
        f"/v1/sessions/{session_id}/messages/async",
        json=payload,
        headers=headers,
    )
    duplicate = client.post(
        f"/v1/sessions/{session_id}/messages/async",
        json=payload,
        headers=headers,
    )

    assert accepted.status_code == 202
    assert accepted.json()["message"]["id"] == "client-message-1"
    assert duplicate.status_code == 202
    assert duplicate.json()["message"]["id"] == "client-message-1"

    deadline = time.monotonic() + 3
    updates = None
    while time.monotonic() < deadline:
        updates = client.get(
            f"/v1/sessions/{session_id}/updates?after=client-message-1",
            headers=headers,
        )
        body = updates.json()
        if body["reply_state"] == "idle" and body["messages"]:
            break
        time.sleep(0.05)

    assert updates is not None
    assert updates.status_code == 200
    assert updates.json()["reply_state"] == "idle"
    assistant = updates.json()["messages"][-1]
    assert assistant["role"] == "assistant"
    assert assistant["content"] == "echo: 你在吗？"
    history = client.get(
        f"/v1/sessions/{session_id}/messages?limit=100",
        headers=headers,
    ).json()["messages"]
    assert [item["id"] for item in history].count("client-message-1") == 1


def test_async_message_can_settle_silently(client) -> None:
    register = client.post(
        "/v1/auth/register",
        json={"email": "silent-chat@example.com", "password": "test1234"},
    )
    headers = {"Authorization": f"Bearer {register.json()['token']}"}
    session_id = client.post(
        "/v1/sessions", json={"user_id": "ignored"}, headers=headers
    ).json()["id"]
    client.app.state.container.chat_reply_service.debounce_seconds = 0

    response = client.post(
        f"/v1/sessions/{session_id}/messages/async",
        json={"client_message_id": "silent-1", "content": "好的"},
        headers=headers,
    )
    assert response.status_code == 202

    deadline = time.monotonic() + 2
    state = "queued"
    while time.monotonic() < deadline:
        body = client.get(
            f"/v1/sessions/{session_id}/updates?after=silent-1",
            headers=headers,
        ).json()
        state = body["reply_state"]
        if state == "idle":
            break
        time.sleep(0.05)

    assert state == "idle"
    history = client.get(
        f"/v1/sessions/{session_id}/messages?limit=100",
        headers=headers,
    ).json()["messages"]
    assert [item["role"] for item in history] == ["user"]
    assert history[0]["reply_disposition"] == "silent"


def test_account_deletion_clears_conversation_observations_and_events(client) -> None:
    register = client.post(
        "/v1/auth/register",
        json={"email": "delete-events@example.com", "password": "test1234"},
    )
    token = register.json()["token"]
    user_id = register.json()["user"]["id"]
    headers = {"Authorization": f"Bearer {token}"}
    session = client.post("/v1/sessions", json={"user_id": user_id}, headers=headers)
    session_id = session.json()["id"]
    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"content": "我搬家了"},
        headers=headers,
    )
    assert response.status_code == 200

    container = client.app.state.container
    container.event_store.upsert_candidate(
        user_id=user_id,
        agent_id="default",
        candidate=EventCandidate(
            event_key="move_2026_09",
            kind="life_event",
            title="搬家",
            core_summary="用户搬家了",
            status=EventStatus.completed,
            occurred_start=datetime(2026, 9, 2, tzinfo=timezone.utc),
            time_precision=TimePrecision.day,
        ),
        origin=OriginClass.owner,
        source_ref=f"session:{session_id}:message:test",
    )

    deleted = client.delete("/v1/auth/account", headers=headers)

    assert deleted.status_code == 204
    assert container.event_store.query(user_id) == []
    assert container.observation_store.query(user_id) == []


def test_presence_and_proactive_inbox(client) -> None:
    register_response = client.post(
        "/v1/auth/register",
        json={"email": "alice@example.com", "password": "test1234"},
    )
    assert register_response.status_code == 201
    token = register_response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    heartbeat_response = client.post("/v1/presence/heartbeat", headers=headers)
    assert heartbeat_response.status_code == 200
    assert heartbeat_response.json()["status"] == "ok"

    inbox_response = client.get("/v1/proactive/inbox", headers=headers)
    assert inbox_response.status_code == 200
    inbox_messages = inbox_response.json()["messages"]
    # A brand-new user starts with proactive messaging off, so the inbox is
    # empty until they opt in from the account center.
    assert inbox_messages == []

    ack_response = client.post(
        "/v1/proactive/inbox/ack",
        json={"ids": []},
        headers=headers,
    )
    assert ack_response.status_code == 200
    assert ack_response.json()["acknowledged"] == 0


def test_device_registration(client) -> None:
    register_response = client.post(
        "/v1/auth/register",
        json={"email": "carol@example.com", "password": "test1234"},
    )
    assert register_response.status_code == 201
    token = register_response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    register_device = client.post(
        "/v1/devices",
        json={"token": "device-token-1"},
        headers=headers,
    )
    assert register_device.status_code == 200
    assert register_device.json()["registered"] is True

    duplicate = client.post(
        "/v1/devices",
        json={"token": "device-token-1"},
        headers=headers,
    )
    assert duplicate.json()["registered"] is False

    unregister = client.delete("/v1/devices/device-token-1", headers=headers)
    assert unregister.status_code == 200
    assert unregister.json()["unregistered"] is True


def test_proactive_settings_toggle(client) -> None:
    register_response = client.post(
        "/v1/auth/register",
        json={"email": "dave@example.com", "password": "test1234"},
    )
    assert register_response.status_code == 201
    token = register_response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    initial = client.get("/v1/proactive/settings", headers=headers)
    assert initial.status_code == 200
    # New users start with proactive messaging off; the account-center toggle is
    # opt-in rather than opt-out.
    assert initial.json()["enabled"] is False

    enabled = client.put(
        "/v1/proactive/settings",
        json={"enabled": True},
        headers=headers,
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True

    re_read = client.get("/v1/proactive/settings", headers=headers)
    assert re_read.json()["enabled"] is True

    disabled = client.put(
        "/v1/proactive/settings",
        json={"enabled": False},
        headers=headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


def test_proactive_stats_endpoint(client) -> None:
    register_response = client.post(
        "/v1/auth/register",
        json={"email": "erin@example.com", "password": "test1234"},
    )
    assert register_response.status_code == 201
    token = register_response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    response = client.get("/v1/proactive/stats", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["phase"] in {"dense", "slow", "done", "onboarding", "daily"}
    assert isinstance(body["profile_completeness"], float)
    assert isinstance(body["categories"], list)


def test_login_distinguishes_unregistered_and_wrong_password(client) -> None:
    unregistered = client.post(
        "/v1/auth/login",
        json={"email": "nobody@example.com", "password": "test1234"},
    )
    assert unregistered.status_code == 422
    assert unregistered.json()["error"]["message"] == "邮箱或密码错误"

    client.post(
        "/v1/auth/register",
        json={"email": "frank@example.com", "password": "test1234"},
    )
    wrong_password = client.post(
        "/v1/auth/login",
        json={"email": "frank@example.com", "password": "wrong-pass"},
    )
    assert wrong_password.status_code == 422
    assert wrong_password.json()["error"]["message"] == "邮箱或密码错误"

    correct = client.post(
        "/v1/auth/login",
        json={"email": "frank@example.com", "password": "test1234"},
    )
    assert correct.status_code == 200
    assert correct.json()["user"]["email"] == "frank@example.com"


def test_password_change_api_sends_to_current_user_and_rotates_token(
    client,
    tmp_dir: Path,
) -> None:
    class RecordingSender(EmailSender):
        def __init__(self) -> None:
            self.messages: list[dict[str, str]] = []

        async def send_verification_code(
            self,
            *,
            recipient: str,
            code: str,
            expires_minutes: int,
            action: str,
        ) -> None:
            self.messages.append(
                {
                    "recipient": recipient,
                    "code": code,
                    "expires_minutes": str(expires_minutes),
                    "action": action,
                }
            )

    registered = client.post(
        "/v1/auth/register",
        json={"email": "password-owner@example.com", "password": "old-password"},
    )
    old_token = registered.json()["token"]
    old_headers = {"Authorization": f"Bearer {old_token}"}
    assert client.post("/v1/auth/password/code").status_code == 401

    sender = RecordingSender()
    verification = EmailVerificationService(
        VerificationCodeStore(
            tmp_dir / "password-codes.db",
            secret="test-secret",
        ),
        sender,
    )
    client.app.state.container.auth_service.email_verification = verification

    sent = client.post("/v1/auth/password/code", headers=old_headers)
    assert sent.status_code == 200
    assert sender.messages[-1]["recipient"] == "password-owner@example.com"

    changed = client.post(
        "/v1/auth/password",
        headers=old_headers,
        json={
            "verification_code": sender.messages[-1]["code"],
            "new_password": "new-password",
        },
    )
    assert changed.status_code == 200
    new_token = changed.json()["token"]
    new_headers = {"Authorization": f"Bearer {new_token}"}
    assert client.get("/v1/billing/balance", headers=old_headers).status_code == 401
    assert client.get("/v1/billing/balance", headers=new_headers).status_code == 200

    old_login = client.post(
        "/v1/auth/login",
        json={"email": "password-owner@example.com", "password": "old-password"},
    )
    new_login = client.post(
        "/v1/auth/login",
        json={"email": "password-owner@example.com", "password": "new-password"},
    )
    assert old_login.status_code == 422
    assert new_login.status_code == 200

    reused = client.post(
        "/v1/auth/password",
        headers=new_headers,
        json={
            "verification_code": sender.messages[-1]["code"],
            "new_password": "another-password",
        },
    )
    assert reused.status_code == 422


def test_sleep_scores_endpoint_returns_shadow_dual_scores(client) -> None:
    register_response = client.post(
        "/v1/auth/register",
        json={"email": "sleep@example.com", "password": "test1234"},
    )
    token = register_response.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    samples = [
        {
            "metric_type": "SLEEP_SESSION",
            "day": "2026-08-20",
            "bucket_start": "2026-08-19T15:00:00Z",
            "bucket_end": "2026-08-20T00:00:00Z",
            "value1": 540,
            "value2": 480,
            "value4": "main",
            "source": "test",
        }
    ]
    for index in range(28):
        hour = index // 4
        minute = (index % 4) * 15
        start_hour = 17 + hour
        day = "2026-08-19" if start_hour < 24 else "2026-08-20"
        start_hour %= 24
        end_minute = minute + 15
        end_hour = start_hour + end_minute // 60
        end_minute %= 60
        end_day = day
        if end_hour == 24:
            end_hour = 0
            end_day = "2026-08-20"
        samples.append(
            {
                "metric_type": "HEART_RATE",
                "day": day,
                "bucket_start": f"{day}T{start_hour:02d}:{minute:02d}:00Z",
                "bucket_end": f"{end_day}T{end_hour:02d}:{end_minute:02d}:00Z",
                "value1": 62 - min(index, 12) * 0.25,
            }
        )

    sync = client.post(
        "/v1/health/sync",
        headers=headers,
        json={
            "from_day": "2026-08-20",
            "to_day": "2026-08-20",
            "metric_types": ["SLEEP"],
            "metrics": [],
            "samples": samples,
        },
    )
    assert sync.status_code == 200

    response = client.get(
        "/v1/health/sleep-scores?from_day=2026-08-20&to_day=2026-08-20",
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["visible"] is False
    assert len(body["scores"]) == 1
    score = body["scores"][0]
    assert score["sleep_day"] == "2026-08-20"
    assert score["sleep_health"]["status"] == "calibrating"
    assert score["recovery"]["score"] is not None
    assert score["recovery"]["confidence"] <= 60
    assert "sleep_hrv" in score["recovery"]["missing"]
