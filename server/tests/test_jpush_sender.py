from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import httpx

from app.proactive.jpush_sender import JpushSender
from app.proactive.models import ProactiveDecision, TriggerType


def _decision(message: str) -> ProactiveDecision:
    return ProactiveDecision(
        user_id="u1",
        agent_id="default",
        trigger_type=TriggerType.time,
        message=message,
    )


def test_jpush_builds_android_payload() -> None:
    sender = JpushSender("appkey", "secret")
    payload = sender._build_payload(_decision("昨晚睡得有点晚"), ["tok-1", "tok-2"])

    assert payload["platform"] == "android"
    assert payload["audience"]["registration_id"] == ["tok-1", "tok-2"]
    assert payload["notification"]["alert"] == "昨晚睡得有点晚"
    assert payload["notification"]["android"]["title"] == "Auri"
    assert payload["notification"]["android"]["channel_id"] == "auri_messages"
    assert payload["options"]["time_to_live"] == 21_600


def test_jpush_prefers_push_message_over_chat_message() -> None:
    sender = JpushSender("appkey", "secret")
    decision = _decision("昨晚睡得有点晚，今天要不要早点休息？")
    decision.push_message = "昨晚睡得有点晚"
    payload = sender._build_payload(decision, ["tok-1"])

    assert payload["notification"]["alert"] == "昨晚睡得有点晚"


def test_jpush_uses_basic_auth() -> None:
    sender = JpushSender("appkey", "secret")
    expected = base64.b64encode(b"appkey:secret").decode("ascii")
    assert sender._auth_header() == f"Basic {expected}"


def test_jpush_send_posts_to_api() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"msg_id": "987654"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = JpushSender("appkey", "secret", client=client)

    asyncio.run(sender.send(_decision("提醒"), ["tok-1"]))

    assert captured["url"] == "https://api.jpush.cn/v3/push"
    assert captured["body"]["audience"]["registration_id"] == ["tok-1"]
    assert captured["auth"].startswith("Basic ")


def test_jpush_skips_without_device_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should be sent without device tokens")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = JpushSender("appkey", "secret", client=client)

    asyncio.run(sender.send(_decision("提醒"), []))


def test_jpush_rejection_does_not_raise() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": 1011, "message": "audience invalid"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sender = JpushSender("appkey", "secret", client=client)

    asyncio.run(sender.send(_decision("提醒"), ["tok-1"]))
