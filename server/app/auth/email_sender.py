from __future__ import annotations

from html import escape

import httpx


class EmailDeliveryError(RuntimeError):
    """Raised when the configured email provider cannot accept a message."""


class EmailSender:
    async def send_verification_code(
        self,
        *,
        recipient: str,
        code: str,
        expires_minutes: int,
        action: str,
    ) -> None:
        raise NotImplementedError


class NullEmailSender(EmailSender):
    async def send_verification_code(
        self,
        *,
        recipient: str,
        code: str,
        expires_minutes: int,
        action: str,
    ) -> None:
        del recipient, code, expires_minutes, action
        raise EmailDeliveryError("email delivery is not configured")


class ResendEmailSender(EmailSender):
    API_URL = "https://api.resend.com/emails"

    def __init__(
        self,
        *,
        api_key: str,
        from_address: str,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.api_key = api_key
        self.from_address = from_address
        self.timeout_seconds = timeout_seconds

    async def send_verification_code(
        self,
        *,
        recipient: str,
        code: str,
        expires_minutes: int,
        action: str,
    ) -> None:
        safe_code = escape(code)
        safe_action = escape(action)
        subject = f"{code} 是你的 Auri {action}验证码"
        text = (
            f"你正在进行 Auri {action}，邮箱验证码是：{code}\n\n"
            f"验证码将在 {expires_minutes} 分钟后失效，请勿转发给他人。\n"
            "如果不是你本人操作，请忽略这封邮件。"
        )
        html = f"""
        <div style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:520px;margin:0 auto;padding:32px 24px;color:#17202a">
          <div style="font-size:22px;font-weight:700;margin-bottom:24px">Auri</div>
          <div style="font-size:16px;line-height:1.7">你正在进行 Auri {safe_action}，邮箱验证码是：</div>
          <div style="font-size:34px;font-weight:750;letter-spacing:8px;margin:18px 0 22px;color:#5b5ce2">{safe_code}</div>
          <div style="font-size:14px;line-height:1.7;color:#667085">验证码将在 {expires_minutes} 分钟后失效，请勿转发给他人。</div>
          <div style="font-size:14px;line-height:1.7;color:#667085;margin-top:8px">如果不是你本人操作，请忽略这封邮件。</div>
        </div>
        """.strip()
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Auri-Server/1.0",
        }
        payload = {
            "from": self.from_address,
            "to": [recipient],
            "subject": subject,
            "text": text,
            "html": html,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(self.API_URL, headers=headers, json=payload)
                response.raise_for_status()
        except (httpx.HTTPError, ValueError) as error:
            raise EmailDeliveryError("email provider rejected the message") from error
