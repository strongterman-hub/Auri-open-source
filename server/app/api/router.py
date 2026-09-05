from fastapi import APIRouter

from app.api.routes import (
    agent,
    auth,
    billing,
    devices,
    health,
    health_metrics,
    integrations_xiaomi,
    memory,
    observations,
    presence,
    proactive,
    profile,
    schedule,
    sessions,
    told,
    update,
)

api_router = APIRouter()
api_router.include_router(schedule.router)
api_router.include_router(health.router, tags=["health"])
api_router.include_router(health_metrics.router, prefix="/health", tags=["health-data"])
api_router.include_router(auth.router)
api_router.include_router(billing.router)
api_router.include_router(devices.router)
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(memory.router, prefix="/sessions", tags=["memory"])
api_router.include_router(agent.router, prefix="/sessions", tags=["agent"])
api_router.include_router(observations.router)
api_router.include_router(
    integrations_xiaomi.router, prefix="/integrations/xiaomi", tags=["integrations"]
)
api_router.include_router(presence.router)
api_router.include_router(profile.router)
api_router.include_router(proactive.router)
api_router.include_router(told.router)
api_router.include_router(update.router)
