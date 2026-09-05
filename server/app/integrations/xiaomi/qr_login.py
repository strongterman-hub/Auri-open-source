# Ported from Do1e/mijia-api (GPL-3.0); modified for Auri, 2026.
# See LICENSES/mijia-api-GPL-3.0.txt and THIRD_PARTY_NOTICES.md.
from __future__ import annotations

import asyncio
import base64
import io
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

import httpx
import qrcode

from app.integrations.xiaomi.credential_store import CredentialStore

SERVICE_LOGIN_URL = (
    "https://account.xiaomi.com/pass/serviceLogin?_json=true&sid=mijia&_locale=zh_CN"
)
LOGIN_URL = "https://account.xiaomi.com/longPolling/loginUrl"
LOGIN_PREFIX = b"&&&START&&&"


class XiaomiQrLoginError(RuntimeError):
    """QR login failed or was rejected."""


def _read_login_payload(text: str) -> dict:
    payload = text.encode()
    if not payload.startswith(LOGIN_PREFIX):
        raise XiaomiQrLoginError("unexpected Xiaomi QR login response")
    return json.loads(payload[len(LOGIN_PREFIX) :].decode())


def _handle_ret(text: str, verify_code: bool = True) -> dict:
    data = _read_login_payload(text)
    if verify_code and data.get("code", 0) != 0:
        raise XiaomiQrLoginError(data.get("desc") or data.get("description") or "未知错误")
    return data


def _build_user_agent(locale: str = "zh_CN") -> str:
    ua_id1 = "".join(random.choices("0123456789ABCDEF", k=40))
    ua_id2 = "".join(random.choices("0123456789ABCDEF", k=32))
    ua_id3 = "".join(random.choices("0123456789ABCDEF", k=32))
    ua_id4 = "".join(random.choices("0123456789ABCDEF", k=40))
    pass_o = "".join(random.choices("0123456789abcdef", k=16))
    country = locale.split("_")[1] if "_" in locale else "CN"
    return (
        f"Android-15-11.0.701-Xiaomi-23046RP50C-OS2.0.212.0.VMYCNXM-"
        f"{ua_id1}-{country}-{ua_id3}-{ua_id2}-SmartHome-MI_APP_STORE-"
        f"{ua_id1}|{ua_id4}|{pass_o}-64"
    )


def _build_device_id() -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
    return "".join(random.choices(alphabet, k=16))


def _qr_png_base64(content: str) -> str:
    image = qrcode.make(content)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class XiaomiQrLogin:
    """Low-level Xiaomi account QR login over httpx (port of ``mijiaAPI``)."""

    def __init__(self, locale: str = "zh_CN") -> None:
        self.locale = locale
        self.user_agent = _build_user_agent(locale)
        self.device_id = _build_device_id()

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Connection": "keep-alive",
            "Accept-Encoding": "gzip",
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": (
                f"deviceId={self.device_id};"
                f"pass_o={''.join(random.choices('0123456789abcdef', k=16))};"
                f"passToken=;userId=;cUserId=;uLocale={self.locale};"
            ),
        }

    async def _get_location(self) -> dict:
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            response = await client.get(SERVICE_LOGIN_URL, headers=self._headers())
            response.raise_for_status()
            service_data = _handle_ret(response.text, verify_code=False)
            location = service_data["location"]
            if service_data.get("code") == 0:
                # A fresh QR login has no stored cookies, so this branch should
                # not run. It mirrors mijiaAPI's token-refresh fast path.
                refresh = await client.get(location, headers=self._headers())
                refresh.raise_for_status()
                if refresh.status_code == 200 and refresh.text == "ok":
                    return {"code": 0, "message": "刷新Token成功"}
            query = parse_qs(urlparse(location).query)
            return {key: value[0] for key, value in query.items()}

    async def start(self) -> dict:
        location_data = await self._get_location()
        if location_data.get("code", -1) == 0:
            raise XiaomiQrLoginError("unexpected token refresh during QR login")

        location_data.update(
            {
                "theme": "",
                "bizDeviceType": "",
                "_hasLogo": "false",
                "_qrsize": "240",
                "_dc": str(int(datetime.now().timestamp() * 1000)),
            }
        )
        url = LOGIN_URL + "?" + urlencode(location_data)
        async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
            response = await client.get(url, headers=self._headers())
            response.raise_for_status()
            login_data = _handle_ret(response.text)
        return login_data

    async def poll(self, login_data: dict) -> dict[str, str]:
        lp_url = login_data["lp"]
        timeout = int(login_data.get("timeout") or 300)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout + 10.0), follow_redirects=False, trust_env=False
        ) as client:
            try:
                response = await client.get(lp_url, headers=self._headers())
            except httpx.TimeoutException as exc:
                raise XiaomiQrLoginError("扫码超时，请重试") from exc
            response.raise_for_status()
            auth = _handle_ret(response.text)

        callback_url = auth.get("location")
        if callback_url:
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                await client.get(callback_url, headers=self._headers())

        return {
            "userId": str(auth["userId"]),
            "passToken": str(auth["passToken"]),
        }


@dataclass
class QrLoginSession:
    session_id: str
    auri_user_id: str
    status: str = "pending"
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(UTC) + timedelta(minutes=5)
    )


class QrLoginManager:
    """In-memory QR login sessions that poll Xiaomi in the background."""

    def __init__(
        self,
        credential_store: CredentialStore,
        ttl_seconds: int = 300,
    ) -> None:
        self._credential_store = credential_store
        self._ttl_seconds = ttl_seconds
        self._sessions: dict[str, QrLoginSession] = {}
        self._login = XiaomiQrLogin()

    async def start(self, auri_user_id: str) -> tuple[str, str]:
        login_data = await self._login.start()
        session_id = uuid4().hex
        session = QrLoginSession(
            session_id=session_id,
            auri_user_id=auri_user_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=self._ttl_seconds),
        )
        self._sessions[session_id] = session
        asyncio.create_task(self._poll(session_id, login_data))
        return session_id, _qr_png_base64(login_data["loginUrl"])

    async def _poll(self, session_id: str, login_data: dict) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        try:
            auth = await self._login.poll(login_data)
        except Exception as exc:
            session.status = "failed"
            session.error = str(exc)
            return

        self._credential_store.save(
            session.auri_user_id,
            mi_user_id=auth["userId"],
            pass_token=auth["passToken"],
        )
        session.status = "connected"
        session.error = None

    def status(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            return {"status": "not_found"}
        if session.status == "pending" and datetime.now(UTC) > session.expires_at:
            session.status = "expired"
            session.error = "二维码已过期"
        return {
            "status": session.status,
            "error": session.error,
        }
