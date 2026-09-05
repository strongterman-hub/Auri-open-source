from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Callable

from app.auth.email_sender import EmailDeliveryError, EmailSender
from app.core.errors import ServiceUnavailableError, TooManyRequestsError, ValidationError


PURPOSE_REGISTER = "register"
PURPOSE_PASSWORD_CHANGE = "password_change"
PURPOSE_PASSWORD_RESET = "password_reset"


@dataclass(frozen=True)
class VerificationCodeResult:
    expires_in_seconds: int
    resend_after_seconds: int


@dataclass(frozen=True)
class _IssuedCode:
    row_id: int
    code: str


class _RateLimited(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("verification code request rate limited")
        self.retry_after_seconds = max(1, retry_after_seconds)


class VerificationCodeStore:
    """SQLite-backed, single-use email verification challenges."""

    def __init__(
        self,
        db_path: Path,
        *,
        secret: str,
        ttl_seconds: int = 600,
        resend_after_seconds: int = 60,
        max_attempts: int = 5,
        max_sends_per_email_hour: int = 5,
        max_sends_per_client_hour: int = 20,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.secret = secret.encode("utf-8")
        self.ttl_seconds = max(60, ttl_seconds)
        self.resend_after_seconds = max(1, resend_after_seconds)
        self.max_attempts = max(1, max_attempts)
        self.max_sends_per_email_hour = max(1, max_sends_per_email_hour)
        self.max_sends_per_client_hour = max(1, max_sends_per_client_hour)
        self.clock = clock
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS email_verification_codes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    code_digest TEXT NOT NULL,
                    client_digest TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    next_send_at REAL NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    consumed_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_email_codes_lookup
                    ON email_verification_codes(email, purpose, id DESC);
                CREATE INDEX IF NOT EXISTS idx_email_codes_client
                    ON email_verification_codes(client_digest, created_at);
                """
            )

    def _digest(self, value: str) -> str:
        return hmac.new(self.secret, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def _code_digest(self, email: str, purpose: str, code: str) -> str:
        return self._digest(f"code:{purpose}:{email}:{code}")

    def _client_digest(self, client_key: str) -> str:
        return self._digest(f"client:{client_key or 'unknown'}")

    def issue(self, *, email: str, purpose: str, client_key: str) -> _IssuedCode:
        now = self.clock()
        hour_ago = now - 3600
        client_digest = self._client_digest(client_key)
        code = f"{secrets.randbelow(1_000_000):06d}"
        code_digest = self._code_digest(email, purpose, code)

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                """
                SELECT next_send_at
                FROM email_verification_codes
                WHERE email = ? AND purpose = ?
                ORDER BY id DESC LIMIT 1
                """,
                (email, purpose),
            ).fetchone()
            if latest is not None and float(latest["next_send_at"]) > now:
                raise _RateLimited(ceil(float(latest["next_send_at"]) - now))

            email_count = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM email_verification_codes
                WHERE email = ? AND purpose = ? AND created_at >= ?
                """,
                (email, purpose, hour_ago),
            ).fetchone()["total"]
            if int(email_count) >= self.max_sends_per_email_hour:
                raise _RateLimited(3600)

            client_count = connection.execute(
                """
                SELECT COUNT(*) AS total
                FROM email_verification_codes
                WHERE client_digest = ? AND created_at >= ?
                """,
                (client_digest, hour_ago),
            ).fetchone()["total"]
            if int(client_count) >= self.max_sends_per_client_hour:
                raise _RateLimited(3600)

            connection.execute(
                """
                UPDATE email_verification_codes
                SET consumed_at = ?
                WHERE email = ? AND purpose = ? AND consumed_at IS NULL
                """,
                (now, email, purpose),
            )
            cursor = connection.execute(
                """
                INSERT INTO email_verification_codes (
                    email, purpose, code_digest, client_digest, created_at,
                    expires_at, next_send_at, attempts, consumed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL)
                """,
                (
                    email,
                    purpose,
                    code_digest,
                    client_digest,
                    now,
                    now + self.ttl_seconds,
                    now + self.resend_after_seconds,
                ),
            )
            connection.execute(
                "DELETE FROM email_verification_codes WHERE created_at < ?",
                (now - 86400 * 7,),
            )
            return _IssuedCode(row_id=int(cursor.lastrowid), code=code)

    def invalidate_after_delivery_failure(self, row_id: int) -> None:
        now = self.clock()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE email_verification_codes
                SET consumed_at = ?, next_send_at = ?
                WHERE id = ?
                """,
                (now, now, row_id),
            )

    def verify(self, *, email: str, purpose: str, code: str) -> bool:
        now = self.clock()
        candidate_digest = self._code_digest(email, purpose, code)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, code_digest, expires_at, attempts
                FROM email_verification_codes
                WHERE email = ? AND purpose = ? AND consumed_at IS NULL
                ORDER BY id DESC LIMIT 1
                """,
                (email, purpose),
            ).fetchone()
            if row is None:
                return False

            row_id = int(row["id"])
            attempts = int(row["attempts"])
            if float(row["expires_at"]) < now or attempts >= self.max_attempts:
                connection.execute(
                    "UPDATE email_verification_codes SET consumed_at = ? WHERE id = ?",
                    (now, row_id),
                )
                return False

            if hmac.compare_digest(str(row["code_digest"]), candidate_digest):
                connection.execute(
                    "UPDATE email_verification_codes SET consumed_at = ? WHERE id = ?",
                    (now, row_id),
                )
                return True

            next_attempts = attempts + 1
            connection.execute(
                """
                UPDATE email_verification_codes
                SET attempts = ?, consumed_at = CASE WHEN ? >= ? THEN ? ELSE consumed_at END
                WHERE id = ?
                """,
                (next_attempts, next_attempts, self.max_attempts, now, row_id),
            )
            return False


class EmailVerificationService:
    def __init__(self, store: VerificationCodeStore, sender: EmailSender) -> None:
        self.store = store
        self.sender = sender

    async def request_registration_code(
        self,
        *,
        email: str,
        client_key: str,
    ) -> VerificationCodeResult:
        return await self._request_code(
            email=email,
            purpose=PURPOSE_REGISTER,
            client_key=client_key,
        )

    async def request_password_change_code(
        self,
        *,
        email: str,
        client_key: str,
    ) -> VerificationCodeResult:
        return await self._request_code(
            email=email,
            purpose=PURPOSE_PASSWORD_CHANGE,
            client_key=client_key,
        )

    async def request_password_reset_code(
        self,
        *,
        email: str,
        client_key: str,
    ) -> VerificationCodeResult:
        return await self._request_code(
            email=email,
            purpose=PURPOSE_PASSWORD_RESET,
            client_key=client_key,
        )

    async def _request_code(
        self,
        *,
        email: str,
        purpose: str,
        client_key: str,
    ) -> VerificationCodeResult:
        try:
            issued = self.store.issue(
                email=email,
                purpose=purpose,
                client_key=client_key,
            )
        except _RateLimited as error:
            raise TooManyRequestsError(
                message=f"请求过于频繁，请在 {error.retry_after_seconds} 秒后重试",
                retry_after_seconds=error.retry_after_seconds,
            ) from error

        try:
            await self.sender.send_verification_code(
                recipient=email,
                code=issued.code,
                expires_minutes=max(1, ceil(self.store.ttl_seconds / 60)),
                action=(
                    "重置密码"
                    if purpose == PURPOSE_PASSWORD_RESET
                    else "修改密码"
                    if purpose == PURPOSE_PASSWORD_CHANGE
                    else "注册"
                ),
            )
        except EmailDeliveryError as error:
            self.store.invalidate_after_delivery_failure(issued.row_id)
            raise ServiceUnavailableError(message="验证码邮件暂时发送失败，请稍后重试") from error

        return VerificationCodeResult(
            expires_in_seconds=self.store.ttl_seconds,
            resend_after_seconds=self.store.resend_after_seconds,
        )

    def verify_registration_code(self, *, email: str, code: str) -> None:
        if not self.store.verify(email=email, purpose=PURPOSE_REGISTER, code=code):
            raise ValidationError(message="验证码错误或已失效")

    def verify_password_change_code(self, *, email: str, code: str) -> None:
        if not self.store.verify(
            email=email,
            purpose=PURPOSE_PASSWORD_CHANGE,
            code=code,
        ):
            raise ValidationError(message="验证码错误或已失效")

    def verify_password_reset_code(self, *, email: str, code: str) -> None:
        if not self.store.verify(
            email=email,
            purpose=PURPOSE_PASSWORD_RESET,
            code=code,
        ):
            raise ValidationError(message="验证码错误或已失效")
