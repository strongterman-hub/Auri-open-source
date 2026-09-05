from __future__ import annotations

from datetime import datetime

from app.auth.models import (
    AuthRegisterRequest,
    AuthResponse,
    AuthUser,
    PasswordChangeRequest,
    PasswordResetRequest,
    normalize_email,
)
from app.auth.store import AuthStore
from app.auth.verification import EmailVerificationService, VerificationCodeResult
from app.core.errors import ConflictError, ServiceUnavailableError, ValidationError


class AuthService:
    def __init__(
        self,
        store: AuthStore,
        *,
        email_verification: EmailVerificationService | None = None,
        email_verification_required: bool = False,
    ) -> None:
        self.store = store
        self.email_verification = email_verification
        self.email_verification_required = email_verification_required

    async def request_registration_code(
        self,
        email: str,
        *,
        client_key: str,
    ) -> VerificationCodeResult:
        normalized_email = normalize_email(email)
        if self.store.user_exists(normalized_email):
            raise ConflictError(message="该邮箱已注册")
        if self.email_verification is None:
            raise ServiceUnavailableError(message="邮箱验证码服务尚未配置")
        return await self.email_verification.request_registration_code(
            email=normalized_email,
            client_key=client_key,
        )

    def register(self, payload: AuthRegisterRequest) -> AuthResponse:
        if self.email_verification_required:
            if self.email_verification is None or not payload.verification_code:
                raise ValidationError(message="请输入邮箱验证码")
            self.email_verification.verify_registration_code(
                email=payload.email,
                code=payload.verification_code,
            )
        try:
            user = self.store.create_user(payload.email, payload.password)
        except ValueError as error:
            raise ConflictError(message=str(error)) from error
        token = self.store.create_token(user.id)
        return AuthResponse(token=token, user=user)

    async def request_password_change_code(
        self,
        user_id: str,
        *,
        client_key: str,
    ) -> VerificationCodeResult:
        if not self.store.user_exists(user_id):
            raise ValidationError(message="账号不存在")
        if self.email_verification is None:
            raise ServiceUnavailableError(message="邮箱验证码服务尚未配置")
        return await self.email_verification.request_password_change_code(
            email=user_id,
            client_key=client_key,
        )

    async def request_password_reset_code(
        self,
        email: str,
        *,
        client_key: str,
    ) -> VerificationCodeResult:
        normalized_email = normalize_email(email)
        if self.email_verification is None:
            raise ServiceUnavailableError(message="邮箱验证码服务尚未配置")
        # Do not reveal whether an account exists through this public endpoint.
        if not self.store.user_exists(normalized_email):
            return VerificationCodeResult(
                expires_in_seconds=self.email_verification.store.ttl_seconds,
                resend_after_seconds=self.email_verification.store.resend_after_seconds,
            )
        return await self.email_verification.request_password_reset_code(
            email=normalized_email,
            client_key=client_key,
        )

    def change_password(
        self,
        user_id: str,
        payload: PasswordChangeRequest,
    ) -> AuthResponse:
        if not self.store.user_exists(user_id):
            raise ValidationError(message="账号不存在")
        if self.email_verification is None:
            raise ServiceUnavailableError(message="邮箱验证码服务尚未配置")
        self.email_verification.verify_password_change_code(
            email=user_id,
            code=payload.verification_code,
        )
        try:
            user, token = self.store.change_password_and_rotate_token(
                user_id,
                payload.new_password,
            )
        except ValueError as error:
            raise ValidationError(message=str(error)) from error
        return AuthResponse(token=token, user=user)

    def reset_password(self, payload: PasswordResetRequest) -> AuthResponse:
        if self.email_verification is None:
            raise ServiceUnavailableError(message="邮箱验证码服务尚未配置")
        # Use the same user-facing error for an unknown email and an invalid code.
        if not self.store.user_exists(payload.email):
            raise ValidationError(message="验证码错误或已失效")
        self.email_verification.verify_password_reset_code(
            email=payload.email,
            code=payload.verification_code,
        )
        try:
            user, token = self.store.change_password_and_rotate_token(
                payload.email,
                payload.new_password,
            )
        except ValueError as error:
            raise ValidationError(message="验证码错误或已失效") from error
        return AuthResponse(token=token, user=user)

    def login(self, email: str, password: str) -> AuthResponse:
        if not self.store.user_exists(email):
            raise ValidationError(message="邮箱或密码错误")
        user = self.store.verify_user(email, password)
        if user is None:
            raise ValidationError(message="邮箱或密码错误")
        token = self.store.create_token(user.id)
        return AuthResponse(token=token, user=user)

    def logout(self, token: str) -> None:
        self.store.revoke_token(token)

    def get_user(self, token: str) -> AuthUser | None:
        user_id = self.store.resolve_user_id(token)
        if user_id is None:
            return None
        return AuthUser(
            id=user_id,
            email=user_id,
            created_at=datetime.min,
        )
