from __future__ import annotations

import base64
import logging
from typing import Any

import httpx

from app.proactive.models import ProactiveDecision
from app.proactive.push import PushSender


class JpushSender(PushSender):
    """Sends Android push notifications through JPush."""

    endpoint = "https://api.jpush.cn/v3/push"
    message_channel_id = "auri_messages"
    time_to_live_seconds = 6 * 60 * 60

    def __init__(
        self,
        app_key: str,
        master_secret: str,
        *,
        title: str = "Auri",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.app_key = app_key
        self.master_secret = master_secret
        self.title = title
        self.logger = logging.getLogger("auri.jpush")
        self._client = client

    def _auth_header(self) -> str:
        token = f"{self.app_key}:{self.master_secret}"
        encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"

    def _build_payload(
        self,
        decision: ProactiveDecision,
        device_tokens: list[str],
    ) -> dict[str, Any]:
        alert = decision.push_message or decision.message or "新消息"
        return {
            "platform": "android",
            "audience": {"registration_id": device_tokens},
            "notification": {
                "alert": alert,
                "android": {
                    "alert": alert,
                    "title": self.title,
                    "channel_id": self.message_channel_id,
                },
            },
            "options": {
                "time_to_live": self.time_to_live_seconds,
            },
        }

    async def send(
        self,
        decision: ProactiveDecision,
        device_tokens: list[str],
    ) -> bool:
        if not device_tokens:
            self.logger.info(
                "jpush skip user=%s: no registered device tokens",
                decision.user_id,
            )
            return False

        payload = self._build_payload(decision, device_tokens)
        headers = {
            "Authorization": self._auth_header(),
            "Content-Type": "application/json",
        }
        try:
            response = await self._post(payload, headers)
        except httpx.HTTPError as exc:
            self.logger.warning(
                "jpush network error user=%s: %s",
                decision.user_id,
                exc,
            )
            return False

        if response.status_code == 200:
            data = response.json()
            self.logger.info(
                "jpush sent user=%s msg_id=%s",
                decision.user_id,
                data.get("msg_id"),
            )
            return True

        self.logger.warning(
            "jpush rejected user=%s status=%s body=%s",
            decision.user_id,
            response.status_code,
            response.text,
        )
        return False

    async def _post(
        self,
        payload: dict[str, Any],
        headers: dict[str, str],
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(self.endpoint, json=payload, headers=headers)
        async with httpx.AsyncClient(timeout=10.0) as client:
            return await client.post(self.endpoint, json=payload, headers=headers)
