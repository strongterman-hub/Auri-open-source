from __future__ import annotations

from contextlib import asynccontextmanager
import mimetypes
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.routes.admin import router as admin_router
from app.chat.scheduler import ChatReplyScheduler
from app.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging
from app.proactive.scheduler import ProactiveScheduler
from app.reminders.scheduler import ReminderScheduler
from app.services.health_sync_scheduler import HealthSyncScheduler
from app.services.weather_scheduler import WeatherScheduler
from app.state.container import create_container

settings = get_settings()
STATIC_DIR = Path(__file__).parent / "static"
SITE_DIR = STATIC_DIR / "site"
mimetypes.add_type("text/javascript", ".js")
SITE_PAGE_HEADERS = {
    "Cache-Control": "no-cache",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; connect-src 'self'; "
        "font-src 'self'; frame-ancestors 'none'; img-src 'self' data:; "
        "object-src 'none'; script-src 'self'; style-src 'self'"
    ),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.container = create_container(settings)
    scheduler: ProactiveScheduler | None = None
    health_sync_scheduler: HealthSyncScheduler | None = None
    weather_scheduler: WeatherScheduler | None = None
    reminder_scheduler: ReminderScheduler | None = None
    chat_reply_scheduler: ChatReplyScheduler | None = None
    if settings.chat_reply_enabled:
        chat_reply_scheduler = ChatReplyScheduler(
            app.state.container.chat_reply_service,
            settings.chat_reply_tick_seconds,
        )
        await chat_reply_scheduler.start()
        app.state.chat_reply_scheduler = chat_reply_scheduler
    if settings.proactive_enabled:
        scheduler = ProactiveScheduler(
            app.state.container.proactive_engine,
            settings.proactive_tick_seconds,
            settings.proactive_onboarding_tick_seconds,
            settings.proactive_onboarding_slow_tick_seconds,
        )
        await scheduler.start()
        app.state.proactive_scheduler = scheduler
    if settings.health_sync_enabled:
        health_sync_scheduler = HealthSyncScheduler(
            auth_store=app.state.container.auth_store,
            credential_store=app.state.container.xiaomi_credential_store,
            xiaomi_service=app.state.container.xiaomi_service,
            tick_seconds=settings.health_sync_tick_seconds,
            proactive_engine=app.state.container.proactive_engine,
        )
        await health_sync_scheduler.start()
        app.state.health_sync_scheduler = health_sync_scheduler
    if (
        settings.weather_enabled
        and settings.weather_latitude is not None
        and settings.weather_longitude is not None
    ):
        weather_service = app.state.container.weather_service
        weather_scheduler = WeatherScheduler(
            weather_service=weather_service,
            session_service=app.state.container.session_service,
            proactive_engine=app.state.container.proactive_engine,
            tick_seconds=settings.weather_tick_seconds,
        )
        await weather_scheduler.start()
        app.state.weather_scheduler = weather_scheduler
    if settings.reminder_enabled:
        reminder_scheduler = app.state.container.reminder_scheduler
        await reminder_scheduler.start()
        app.state.reminder_scheduler = reminder_scheduler
    yield
    if chat_reply_scheduler is not None:
        await chat_reply_scheduler.stop()
    if scheduler is not None:
        await scheduler.stop()
    if health_sync_scheduler is not None:
        await health_sync_scheduler.stop()
    if weather_scheduler is not None:
        await weather_scheduler.stop()
    if reminder_scheduler is not None:
        await reminder_scheduler.stop()
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(api_router, prefix=settings.api_prefix)
app.include_router(admin_router)


@app.get("/", include_in_schema=False)
async def root() -> FileResponse:
    return FileResponse(
        SITE_DIR / "index.html",
        media_type="text/html",
        headers=SITE_PAGE_HEADERS,
    )


@app.get("/download", include_in_schema=False)
async def download_page() -> FileResponse:
    return FileResponse(
        SITE_DIR / "download.html",
        media_type="text/html",
        headers=SITE_PAGE_HEADERS,
    )


@app.get("/debug", include_in_schema=False)
async def debug_page() -> FileResponse:
    if not settings.debug_ui_enabled:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(STATIC_DIR / "index.html")
