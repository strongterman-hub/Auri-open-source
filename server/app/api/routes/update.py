from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from app.api.dependencies import ContainerDep
from app.services.update_service import AppUpdateService

router = APIRouter(prefix="/update", tags=["update"])


@router.get("/check")
async def check_update(container: ContainerDep, response: Response) -> dict:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    service = AppUpdateService(container.settings.data_dir)
    release = service.get()
    result = release.public_dict()
    if not service.has_apk() and not release.download_url:
        result["download_url"] = None
    return result


@router.get("/apk", include_in_schema=False)
async def download_apk(container: ContainerDep) -> FileResponse:
    service = AppUpdateService(container.settings.data_dir)
    if not service.has_apk():
        raise HTTPException(status_code=404, detail="APK 尚未上传")
    return FileResponse(
        service.apk_path,
        media_type="application/vnd.android.package-archive",
        filename="auri.apk",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/apk/{sha256}", include_in_schema=False)
async def download_versioned_apk(sha256: str, container: ContainerDep) -> FileResponse:
    service = AppUpdateService(container.settings.data_dir)
    path = service.apk_path_for_sha256(sha256)
    if path is None:
        raise HTTPException(status_code=404, detail="指定版本 APK 不存在")
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename="auri.apk",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
