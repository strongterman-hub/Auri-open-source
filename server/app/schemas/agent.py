from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator


class FileAttachment(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    mime: str = Field(default="application/octet-stream", max_length=128)
    data: str = Field(min_length=1, max_length=13_000_000)


class SendMessageRequest(BaseModel):
    content: str = Field(default="", max_length=64_000)
    images: list[str] = Field(default_factory=list, max_length=4)
    files: list[FileAttachment] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _validate_content_or_attachments(self) -> "SendMessageRequest":
        if not self.content.strip() and not self.images and not self.files:
            raise ValueError("content, images, and files cannot all be empty")
        for image in self.images:
            if not image or not isinstance(image, str):
                raise ValueError("images must be non-empty strings")
            if len(image) > 15_000_000:
                raise ValueError("an image is too large")
        for file in self.files:
            if not file.data:
                raise ValueError("file data must not be empty")
        return self


class SendMessageResponse(BaseModel):
    session_id: str
    message: dict[str, Any]
    usage: dict[str, Any] = Field(default_factory=dict)
    tool_results: list[dict[str, str]] = Field(default_factory=list)
    reset_notice: str | None = None


class AsyncSendMessageRequest(SendMessageRequest):
    client_message_id: str = Field(min_length=1, max_length=128)


class AsyncSendMessageResponse(BaseModel):
    session_id: str
    message: dict[str, Any]
    reply_state: str
    reset_notice: str | None = None


class ChatUpdatesResponse(BaseModel):
    session_id: str
    messages: list[dict[str, Any]] = Field(default_factory=list)
    reply_state: str = "idle"
    commands: list[dict[str, Any]] = Field(default_factory=list)
    next_poll_ms: int = 10_000


class MessagePage(BaseModel):
    messages: list[dict[str, Any]]
    has_more: bool = False
