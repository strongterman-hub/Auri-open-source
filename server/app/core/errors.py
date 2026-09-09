from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base class for expected application errors."""

    status_code: int = 500
    code: str = "internal_error"
    message: str = "An unexpected error occurred."

    def __init__(self, message: str | None = None, **extra: Any) -> None:
        super().__init__(message or self.message)
        self.message = message or self.message
        self.extra = extra


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"
    message = "Resource not found."


class UnauthorizedError(AppError):
    status_code = 401
    code = "unauthorized"
    message = "未登录或登录已过期"


class ValidationError(AppError):
    status_code = 422
    code = "validation_error"
    message = "Invalid request."


class ConflictError(AppError):
    status_code = 409
    code = "conflict"
    message = "The request conflicts with the current state."


class TooManyRequestsError(AppError):
    status_code = 429
    code = "rate_limited"
    message = "Too many requests."


class ServiceUnavailableError(AppError):
    status_code = 503
    code = "service_unavailable"
    message = "The service is temporarily unavailable."


class MemoryWriteError(AppError):
    status_code = 422
    code = "memory_write_error"
    message = "The memory write could not be completed."


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.extra or None,
                }
            },
        )
