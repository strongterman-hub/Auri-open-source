from fastapi import APIRouter, Request, status

from app.api.dependencies import ContainerDep, CurrentUserDep
from app.auth.models import (
    AuthLoginRequest,
    AuthRegisterRequest,
    AuthResponse,
    EmailVerificationCodeRequest,
    EmailVerificationCodeResponse,
    PasswordChangeRequest,
    PasswordResetRequest,
)
from app.core.errors import AppError

router = APIRouter(prefix="/auth", tags=["auth"])


def _registration_client_key(request: Request) -> str:
    peer = request.client.host if request.client is not None else "unknown"
    if peer in {"127.0.0.1", "::1"}:
        forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
        real_ip = request.headers.get("x-real-ip", "").strip()
        proxied = forwarded_for or real_ip
        if proxied:
            return proxied[:200]
    return peer[:200]


@router.post("/register/code", response_model=EmailVerificationCodeResponse)
async def request_registration_code(
    payload: EmailVerificationCodeRequest,
    request: Request,
    container: ContainerDep,
) -> EmailVerificationCodeResponse:
    result = await container.auth_service.request_registration_code(
        payload.email,
        client_key=_registration_client_key(request),
    )
    return EmailVerificationCodeResponse(
        expires_in_seconds=result.expires_in_seconds,
        resend_after_seconds=result.resend_after_seconds,
    )


@router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: AuthRegisterRequest, container: ContainerDep) -> AuthResponse:
    response = container.auth_service.register(payload)
    container.billing_store.ensure_welcome_credit(response.user.id)
    return response


@router.post("/login", response_model=AuthResponse)
async def login(payload: AuthLoginRequest, container: ContainerDep) -> AuthResponse:
    return container.auth_service.login(payload.email, payload.password)


@router.post("/password/code", response_model=EmailVerificationCodeResponse)
async def request_password_change_code(
    request: Request,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> EmailVerificationCodeResponse:
    result = await container.auth_service.request_password_change_code(
        current_user.id,
        client_key=_registration_client_key(request),
    )
    return EmailVerificationCodeResponse(
        expires_in_seconds=result.expires_in_seconds,
        resend_after_seconds=result.resend_after_seconds,
    )


@router.post("/password/reset/code", response_model=EmailVerificationCodeResponse)
async def request_password_reset_code(
    payload: EmailVerificationCodeRequest,
    request: Request,
    container: ContainerDep,
) -> EmailVerificationCodeResponse:
    result = await container.auth_service.request_password_reset_code(
        payload.email,
        client_key=_registration_client_key(request),
    )
    return EmailVerificationCodeResponse(
        expires_in_seconds=result.expires_in_seconds,
        resend_after_seconds=result.resend_after_seconds,
    )


@router.post("/password", response_model=AuthResponse)
async def change_password(
    payload: PasswordChangeRequest,
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> AuthResponse:
    return container.auth_service.change_password(current_user.id, payload)


@router.post("/password/reset", response_model=AuthResponse)
async def reset_password(
    payload: PasswordResetRequest,
    container: ContainerDep,
) -> AuthResponse:
    return container.auth_service.reset_password(payload)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(container: ContainerDep, authorization: str | None = None) -> None:
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token:
        raise AppError(status_code=401, code="unauthorized", message="缺少登录凭证")
    container.auth_service.logout(token)


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    container: ContainerDep,
    current_user: CurrentUserDep,
) -> None:
    await container.account_deletion_service.delete_user(current_user.id)
