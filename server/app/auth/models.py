from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


_EMAIL_LOCAL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+$")


def normalize_email(value: str) -> str:
    email = value.strip().lower()
    if len(email) > 254 or email.count("@") != 1:
        raise ValueError("请输入有效邮箱")
    local, domain = email.split("@", 1)
    if not local or len(local) > 64 or not _EMAIL_LOCAL_RE.fullmatch(local):
        raise ValueError("请输入有效邮箱")
    try:
        ascii_domain = domain.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("请输入有效邮箱") from error
    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not re.fullmatch(r"[A-Za-z0-9-]+", label)
        for label in labels
    ):
        raise ValueError("请输入有效邮箱")
    return f"{local}@{ascii_domain}"


class AuthRegisterRequest(BaseModel):
    email: str
    password: str = Field(min_length=6, max_length=128)
    verification_code: str | None = Field(default=None, pattern=r"^\d{6}$")

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class AuthLoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class EmailVerificationCodeRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class EmailVerificationCodeResponse(BaseModel):
    sent: bool = True
    expires_in_seconds: int
    resend_after_seconds: int


class PasswordChangeRequest(BaseModel):
    new_password: str = Field(min_length=6, max_length=128)
    verification_code: str = Field(pattern=r"^\d{6}$")


class PasswordResetRequest(BaseModel):
    email: str
    new_password: str = Field(min_length=6, max_length=128)
    verification_code: str = Field(pattern=r"^\d{6}$")

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class AuthUser(BaseModel):
    id: str
    email: str
    created_at: datetime


class AuthResponse(BaseModel):
    token: str
    user: AuthUser
