from fastapi import APIRouter

from app.api.dependencies import ContainerDep

router = APIRouter()


@router.get("/health")
async def health(container: ContainerDep) -> dict:
    return {
        "status": "ok",
        "app": container.settings.app_name,
        "environment": container.settings.environment,
    }
