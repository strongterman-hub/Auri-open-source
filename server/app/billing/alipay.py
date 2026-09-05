from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _pem(value: str, label: str) -> bytes:
    stripped = "".join(value.strip().split())
    if "BEGIN" in value:
        return value.strip().encode("ascii") + b"\n"
    lines = "\n".join(stripped[index : index + 64] for index in range(0, len(stripped), 64))
    return f"-----BEGIN {label}-----\n{lines}\n-----END {label}-----\n".encode("ascii")


def read_key(value: str | None, path: Path | None) -> str | None:
    if value and value.strip():
        return value.strip()
    if path and Path(path).exists():
        return Path(path).read_text(encoding="utf-8").strip()
    return None


class AlipayClient:
    def __init__(
        self,
        *,
        app_id: str,
        private_key: str,
        alipay_public_key: str,
        gateway: str,
        notify_url: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.app_id = app_id
        self.gateway = gateway
        self.notify_url = notify_url
        self.timeout_seconds = timeout_seconds
        self._private_key = serialization.load_pem_private_key(
            _pem(private_key, "PRIVATE KEY"), password=None
        )
        self._public_key = serialization.load_pem_public_key(
            _pem(alipay_public_key, "PUBLIC KEY")
        )

    @staticmethod
    def canonical(
        params: dict[str, Any], *, exclude_sign_type: bool = True
    ) -> str:
        excluded = {"sign"}
        if exclude_sign_type:
            excluded.add("sign_type")
        return "&".join(
            f"{key}={value}"
            for key, value in sorted(params.items())
            if value is not None and value != "" and key not in excluded
        )

    def sign(self, params: dict[str, Any]) -> str:
        signature = self._private_key.sign(
            self.canonical(params, exclude_sign_type=False).encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("ascii")

    def verify(self, params: dict[str, Any]) -> bool:
        signature = str(params.get("sign") or "")
        if not signature:
            return False
        try:
            self._public_key.verify(
                base64.b64decode(signature),
                self.canonical(params).encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True

    def verify_content(self, content: str, signature: str) -> bool:
        try:
            self._public_key.verify(
                base64.b64decode(signature),
                content.encode("utf-8"),
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True

    @staticmethod
    def _signed_response_content(raw: str, key: str) -> str:
        marker = json.dumps(key, ensure_ascii=False)
        marker_index = raw.find(marker)
        if marker_index < 0:
            raise ValueError("Alipay response node is missing")
        colon_index = raw.find(":", marker_index + len(marker))
        if colon_index < 0:
            raise ValueError("Alipay response node is invalid")
        value_start = colon_index + 1
        while value_start < len(raw) and raw[value_start].isspace():
            value_start += 1
        _value, consumed = json.JSONDecoder().raw_decode(raw[value_start:])
        return raw[value_start : value_start + consumed]

    def build_order_string(self, order_id: str, amount_yuan: int, credits: int) -> str:
        params: dict[str, str] = {
            "app_id": self.app_id,
            "biz_content": json.dumps(
                {
                    "out_trade_no": order_id,
                    "total_amount": f"{amount_yuan:.2f}",
                    "subject": "Auri AI 助手服务",
                    "product_code": "QUICK_MSECURITY_PAY",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "charset": "utf-8",
            "format": "json",
            "method": "alipay.trade.app.pay",
            "notify_url": self.notify_url,
            "sign_type": "RSA2",
            "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
        }
        params["sign"] = self.sign(params)
        return urlencode(params)

    async def query(self, order_id: str) -> dict[str, Any]:
        params: dict[str, str] = {
            "app_id": self.app_id,
            "biz_content": json.dumps(
                {"out_trade_no": order_id}, ensure_ascii=False, separators=(",", ":")
            ),
            "charset": "utf-8",
            "format": "json",
            "method": "alipay.trade.query",
            "sign_type": "RSA2",
            "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
        }
        params["sign"] = self.sign(params)
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                self.gateway,
                data=params,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded;charset=utf-8"
                },
            )
            response.raise_for_status()
        raw = response.text
        payload = json.loads(raw)
        result = payload.get("alipay_trade_query_response") or {}
        if not isinstance(result, dict):
            raise ValueError("Alipay query response is invalid")
        signature = str(payload.get("sign") or "")
        if not signature:
            code = str(result.get("code") or "unknown")
            sub_code = str(result.get("sub_code") or "unknown")
            raise ValueError(f"Alipay query failed: {code}/{sub_code}")
        signed_content = self._signed_response_content(
            raw, "alipay_trade_query_response"
        )
        if not self.verify_content(signed_content, signature):
            raise ValueError("Alipay query response signature is invalid")
        return result
