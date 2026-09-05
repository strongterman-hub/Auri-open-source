from __future__ import annotations

import base64
import secrets
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from app.api.dependencies import ContainerDep
from app.core.admin_auth import create_session_token, verify_session_token
from app.core.pricing import PRICING
from app.services.update_service import AppUpdateService
from app.services.usage_service import build_turns_report, build_usage_report


STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
COOKIE_NAME = "admin_session"
SESSION_TTL_SECONDS = 60 * 60 * 24

router = APIRouter()


class AdminLoginRequest(BaseModel):
    username: str
    password: str


def _credentials(container: ContainerDep) -> tuple[str | None, str | None]:
    return container.settings.admin_username, container.settings.admin_password


def _authenticated(request: Request, container: ContainerDep) -> str | None:
    username, password = _credentials(container)
    if not username or not password:
        return None
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return verify_session_token(token, password)


def require_admin(request: Request, container: ContainerDep) -> str:
    username = _authenticated(request, container)
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
        )
    return username


@router.get("/admin/login", include_in_schema=False)
async def login_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "login.html")


@router.post("/admin/login")
async def login(
    payload: AdminLoginRequest,
    container: ContainerDep,
    response: Response,
) -> dict:
    username, password = _credentials(container)
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin dashboard is not configured.",
        )
    username_ok = secrets.compare_digest(payload.username.encode(), username.encode())
    password_ok = secrets.compare_digest(payload.password.encode(), password.encode())
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或密码错误",
        )
    token = create_session_token(username, password, SESSION_TTL_SECONDS)
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=True,
    )
    return {"ok": True}


@router.post("/admin/logout")
async def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/admin", include_in_schema=False)
async def admin_page(request: Request, container: ContainerDep) -> Response:
    if _authenticated(request, container) is None:
        return RedirectResponse(url="/admin/login", status_code=status.HTTP_302_FOUND)
    return FileResponse(STATIC_DIR / "admin.html")


@router.get("/admin/api/usage")
async def admin_usage(
    container: ContainerDep,
    days: int = 30,
    limit: int = 200,
    user_id: str | None = None,
    _: str = Depends(require_admin),
) -> dict:
    return build_usage_report(
        container.token_logger.path,
        days=max(1, min(days, 365)),
        limit=max(1, min(limit, 2000)),
        user_id=user_id,
    )


@router.get("/admin/api/turns")
async def admin_turns(
    container: ContainerDep,
    days: int = 30,
    limit: int = 100,
    user_id: str | None = None,
    _: str = Depends(require_admin),
) -> dict:
    return build_turns_report(
        container.turn_logger.path,
        days=max(1, min(days, 365)),
        limit=max(1, min(limit, 2000)),
        user_id=user_id,
    )


@router.get("/admin/api/pricing")
async def admin_pricing(_: str = Depends(require_admin)) -> dict:
    return {
        "currency": "CNY",
        "unit": "元 / 百万 tokens",
        "pricing": PRICING,
        "peak_hours": "北京时间 9:00-12:00、14:00-18:00 为高峰时段，其余为空闲时段（空闲为高峰半价）",
    }


class AdminUpdateRequest(BaseModel):
    version_code: int
    version_name: str
    force: bool = False
    changelog: str = ""
    download_url: str | None = None


class AdminUpdatePushRequest(BaseModel):
    version_name: str = ""
    message: str = "发现新版本，请更新 Auri"


@router.get("/admin/api/update")
async def admin_get_update(
    container: ContainerDep,
    _: str = Depends(require_admin),
) -> dict:
    service = AppUpdateService(container.settings.data_dir)
    release = service.get()
    return {
        **release.public_dict(),
        "apk_exists": service.has_apk(),
        "apk_size_on_disk": service.apk_size_on_disk(),
    }


@router.put("/admin/api/update")
async def admin_set_update(
    payload: AdminUpdateRequest,
    container: ContainerDep,
    _: str = Depends(require_admin),
) -> dict:
    service = AppUpdateService(container.settings.data_dir)
    release = service.set_metadata(
        version_code=payload.version_code,
        version_name=payload.version_name,
        force=payload.force,
        changelog=payload.changelog,
        download_url=payload.download_url,
    )
    return {**release.public_dict(), "apk_exists": service.has_apk()}


@router.post("/admin/api/update/apk")
async def admin_upload_apk(
    request: Request,
    container: ContainerDep,
    _: str = Depends(require_admin),
) -> dict:
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="APK 内容为空")
    service = AppUpdateService(container.settings.data_dir)
    release = service.save_apk(data)
    return {**release.public_dict(), "apk_exists": True}


@router.post("/admin/api/update/push")
async def admin_update_push(
    req: AdminUpdatePushRequest,
    container: ContainerDep,
    _: str = Depends(require_admin),
) -> dict:
    tokens = container.presence_service.all_device_tokens()
    if not tokens:
        return {"sent": 0, "tokens": 0, "detail": "没有已注册的设备"}

    app_key = container.settings.jpush_app_key
    master_secret = container.settings.jpush_master_secret
    if not app_key or not master_secret:
        raise HTTPException(status_code=503, detail="JPush 未配置")

    auth = base64.b64encode(f"{app_key}:{master_secret}".encode("utf-8")).decode("ascii")
    push_payload = {
        "platform": "android",
        "audience": {"registration_id": tokens},
        "message": {
            "msg_content": req.message,
            "content_type": "text",
            "title": "Auri 更新",
            "extras": {
                "type": "force_update_check",
                "version_name": req.version_name,
            },
        },
    }
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://api.jpush.cn/v3/push",
            json=push_payload,
            headers=headers,
        )

    if response.status_code == 200:
        return {
            "sent": 1,
            "tokens": len(tokens),
            "msg_id": response.json().get("msg_id"),
        }
    raise HTTPException(
        status_code=502,
        detail=f"JPush 推送失败: {response.status_code} {response.text}",
    )
