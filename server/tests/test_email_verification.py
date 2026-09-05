from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

from app.api.routes.auth import _registration_client_key
from app.auth.email_sender import EmailDeliveryError, EmailSender
from app.auth.models import AuthRegisterRequest, PasswordChangeRequest, PasswordResetRequest
from app.auth.service import AuthService
from app.auth.store import AuthStore
from app.auth.verification import EmailVerificationService, VerificationCodeStore
from app.core.errors import ServiceUnavailableError, TooManyRequestsError, ValidationError
from app.config import Settings
from app.state.container import create_container


class RecordingEmailSender(EmailSender):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[dict[str, object]] = []

    async def send_verification_code(
        self,
        *,
        recipient: str,
        code: str,
        expires_minutes: int,
        action: str,
    ) -> None:
        if self.fail:
            raise EmailDeliveryError("test delivery failure")
        self.messages.append(
            {
                "recipient": recipient,
                "code": code,
                "expires_minutes": expires_minutes,
                "action": action,
            }
        )


def _service(tmp_dir, now: list[float], sender: EmailSender):
    code_store = VerificationCodeStore(
        tmp_dir / "verification.db",
        secret="test-secret",
        ttl_seconds=600,
        resend_after_seconds=60,
        max_attempts=3,
        max_sends_per_email_hour=3,
        max_sends_per_client_hour=10,
        clock=lambda: now[0],
    )
    verification = EmailVerificationService(code_store, sender)
    auth = AuthService(
        AuthStore(tmp_dir / "auth"),
        email_verification=verification,
        email_verification_required=True,
    )
    return auth, verification


def test_verified_registration_consumes_code_once(tmp_dir) -> None:
    now = [1_800_000_000.0]
    sender = RecordingEmailSender()
    auth, verification = _service(tmp_dir, now, sender)

    delivery = asyncio.run(
        auth.request_registration_code(" New.User@Example.com ", client_key="127.0.0.1")
    )
    assert delivery.expires_in_seconds == 600
    assert delivery.resend_after_seconds == 60
    assert sender.messages[0]["recipient"] == "new.user@example.com"
    code = str(sender.messages[0]["code"])

    response = auth.register(
        AuthRegisterRequest(
            email="new.user@example.com",
            password="test1234",
            verification_code=code,
        )
    )
    assert response.user.id == "new.user@example.com"

    with pytest.raises(ValidationError, match="验证码错误或已失效"):
        verification.verify_registration_code(email="new.user@example.com", code=code)


def test_resend_invalidates_previous_code_and_enforces_cooldown(tmp_dir) -> None:
    now = [1_800_000_000.0]
    sender = RecordingEmailSender()
    auth, verification = _service(tmp_dir, now, sender)

    asyncio.run(auth.request_registration_code("user@example.com", client_key="client-a"))
    old_code = str(sender.messages[-1]["code"])

    with pytest.raises(TooManyRequestsError):
        asyncio.run(auth.request_registration_code("user@example.com", client_key="client-a"))

    now[0] += 60
    asyncio.run(auth.request_registration_code("user@example.com", client_key="client-a"))
    new_code = str(sender.messages[-1]["code"])

    with pytest.raises(ValidationError):
        verification.verify_registration_code(email="user@example.com", code=old_code)
    verification.verify_registration_code(email="user@example.com", code=new_code)


def test_attempt_limit_invalidates_code(tmp_dir) -> None:
    now = [1_800_000_000.0]
    sender = RecordingEmailSender()
    auth, verification = _service(tmp_dir, now, sender)

    asyncio.run(auth.request_registration_code("user@example.com", client_key="client-a"))
    code = str(sender.messages[-1]["code"])
    wrong_code = "000000" if code != "000000" else "111111"
    for _ in range(3):
        with pytest.raises(ValidationError):
            verification.verify_registration_code(email="user@example.com", code=wrong_code)

    with pytest.raises(ValidationError):
        verification.verify_registration_code(email="user@example.com", code=code)


def test_delivery_failure_allows_immediate_retry(tmp_dir) -> None:
    now = [1_800_000_000.0]
    sender = RecordingEmailSender(fail=True)
    auth, verification = _service(tmp_dir, now, sender)

    with pytest.raises(ServiceUnavailableError):
        asyncio.run(auth.request_registration_code("user@example.com", client_key="client-a"))

    replacement = RecordingEmailSender()
    verification.sender = replacement
    asyncio.run(auth.request_registration_code("user@example.com", client_key="client-a"))
    assert len(replacement.messages) == 1


def test_registration_requires_code_when_enabled(tmp_dir) -> None:
    now = [1_800_000_000.0]
    auth, _ = _service(tmp_dir, now, RecordingEmailSender())

    with pytest.raises(ValidationError, match="请输入邮箱验证码"):
        auth.register(AuthRegisterRequest(email="user@example.com", password="test1234"))


def test_password_change_uses_separate_code_and_rotates_tokens(tmp_dir) -> None:
    now = [1_800_000_000.0]
    sender = RecordingEmailSender()
    auth, verification = _service(tmp_dir, now, sender)
    user = auth.store.create_user("owner@example.com", "old-password")
    old_token = auth.store.create_token(user.id)

    result = asyncio.run(
        auth.request_password_change_code(user.id, client_key="client-a")
    )

    assert result.expires_in_seconds == 600
    assert sender.messages[-1]["recipient"] == "owner@example.com"
    assert sender.messages[-1]["action"] == "修改密码"
    code = str(sender.messages[-1]["code"])
    with pytest.raises(ValidationError, match="验证码错误或已失效"):
        verification.verify_registration_code(email=user.id, code=code)

    changed = auth.change_password(
        user.id,
        PasswordChangeRequest(
            new_password="new-password",
            verification_code=code,
        ),
    )

    assert auth.store.resolve_user_id(old_token) is None
    assert auth.store.resolve_user_id(changed.token) == user.id
    assert auth.store.verify_user(user.id, "old-password") is None
    assert auth.store.verify_user(user.id, "new-password") is not None
    with pytest.raises(ValidationError, match="验证码错误或已失效"):
        auth.change_password(
            user.id,
            PasswordChangeRequest(
                new_password="another-password",
                verification_code=code,
            ),
        )


def test_wrong_password_change_code_keeps_password_and_tokens(tmp_dir) -> None:
    now = [1_800_000_000.0]
    sender = RecordingEmailSender()
    auth, _verification = _service(tmp_dir, now, sender)
    user = auth.store.create_user("owner@example.com", "old-password")
    old_token = auth.store.create_token(user.id)
    asyncio.run(auth.request_password_change_code(user.id, client_key="client-a"))
    real_code = str(sender.messages[-1]["code"])
    wrong_code = "000000" if real_code != "000000" else "111111"

    with pytest.raises(ValidationError, match="验证码错误或已失效"):
        auth.change_password(
            user.id,
            PasswordChangeRequest(
                new_password="new-password",
                verification_code=wrong_code,
            ),
        )

    assert auth.store.resolve_user_id(old_token) == user.id
    assert auth.store.verify_user(user.id, "old-password") is not None
    assert auth.store.verify_user(user.id, "new-password") is None


def test_logged_out_password_reset_uses_separate_code_and_rotates_tokens(tmp_dir) -> None:
    now = [1_800_000_000.0]
    sender = RecordingEmailSender()
    auth, _verification = _service(tmp_dir, now, sender)
    user = auth.store.create_user("owner@example.com", "old-password")
    old_token = auth.store.create_token(user.id)

    result = asyncio.run(
        auth.request_password_reset_code(user.id, client_key="client-a")
    )

    assert result.expires_in_seconds == 600
    assert sender.messages[-1]["action"] == "重置密码"
    code = str(sender.messages[-1]["code"])
    changed = auth.reset_password(
        PasswordResetRequest(
            email=user.id,
            new_password="new-password",
            verification_code=code,
        )
    )

    assert auth.store.resolve_user_id(old_token) is None
    assert auth.store.resolve_user_id(changed.token) == user.id
    assert auth.store.verify_user(user.id, "old-password") is None
    assert auth.store.verify_user(user.id, "new-password") is not None


def test_password_reset_does_not_disclose_unknown_email(tmp_dir) -> None:
    now = [1_800_000_000.0]
    sender = RecordingEmailSender()
    auth, _verification = _service(tmp_dir, now, sender)

    result = asyncio.run(
        auth.request_password_reset_code("missing@example.com", client_key="client-a")
    )

    assert result.expires_in_seconds == 600
    assert sender.messages == []
    with pytest.raises(ValidationError, match="验证码错误或已失效"):
        auth.reset_password(
            PasswordResetRequest(
                email="missing@example.com",
                new_password="new-password",
                verification_code="123456",
            )
        )


def test_registration_client_key_trusts_forwarding_only_from_loopback_proxy() -> None:
    headers = [(b"x-forwarded-for", b"203.0.113.8, 127.0.0.1")]
    proxied = Request(
        {"type": "http", "headers": headers, "client": ("127.0.0.1", 12345)}
    )
    direct = Request(
        {"type": "http", "headers": headers, "client": ("198.51.100.5", 12345)}
    )

    assert _registration_client_key(proxied) == "203.0.113.8"
    assert _registration_client_key(direct) == "198.51.100.5"


def test_enabled_verification_requires_delivery_and_persistent_secret(tmp_dir) -> None:
    with pytest.raises(RuntimeError, match="AURI_RESEND_API_KEY"):
        create_container(
            Settings(
                data_dir=tmp_dir / "missing-provider",
                auth_email_verification_required=True,
            )
        )

    with pytest.raises(RuntimeError, match="AURI_AUTH_CODE_SECRET"):
        create_container(
            Settings(
                data_dir=tmp_dir / "missing-secret",
                auth_email_verification_required=True,
                resend_api_key="test-key",
            )
        )
