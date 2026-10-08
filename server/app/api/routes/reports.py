from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.dependencies import ContainerDep, CurrentUserDep

router = APIRouter(prefix='/health/reports', tags=['health-reports'])


class ClientRegistration(BaseModel):
    version_code: int = Field(ge=40, le=100000)


class ReportSettings(BaseModel):
    enabled: bool


class GenerateRequest(BaseModel):
    refresh: bool = False


def service(container):
    if not container.settings.weekly_reports_enabled:
        raise HTTPException(503, '健康周报暂未开启')
    return container.weekly_report_service


@router.post('/client')
async def register(payload: ClientRegistration, container: ContainerDep, user: CurrentUserDep):
    srv = service(container)
    srv.store.register(user.id, payload.version_code)
    srv._wake.set()
    return srv.store.preferences(user.id)


@router.get('/settings')
async def settings(container: ContainerDep, user: CurrentUserDep):
    return service(container).store.preferences(user.id)


@router.put('/settings')
async def save_settings(payload: ReportSettings, container: ContainerDep, user: CurrentUserDep):
    srv = service(container)
    if not srv.store.preferences(user.id)['supported']:
        raise HTTPException(409, '请先更新 Auri 客户端')
    srv.store.set_enabled(user.id, payload.enabled)
    return srv.store.preferences(user.id)


@router.get('')
async def list_reports(container: ContainerDep, user: CurrentUserDep):
    return {'reports': service(container).store.list(user.id)}


@router.post('/weekly', status_code=202)
async def generate(payload: GenerateRequest, container: ContainerDep, user: CurrentUserDep):
    srv = service(container)
    if not srv.store.preferences(user.id)['supported']:
        raise HTTPException(409, '请先更新 Auri 客户端')
    row = srv.request(user.id, payload.refresh)
    return {'id': row['id'], 'status': row['status']}


@router.get('/{report_id}')
async def get_report(report_id: str, container: ContainerDep, user: CurrentUserDep):
    row = service(container).store.get(user.id, report_id)
    if row is None:
        raise HTTPException(404, '这份周报不存在或已删除')
    return {k: row[k] for k in ('id', 'week_start', 'week_end', 'timezone', 'status', 'summary', 'html', 'created_at', 'updated_at', 'error', 'revision')}


@router.post('/{report_id}/refresh', status_code=202)
async def refresh(report_id: str, container: ContainerDep, user: CurrentUserDep):
    srv = service(container)
    row = srv.store.get(user.id, report_id)
    if row is None:
        raise HTTPException(404, '这份周报不存在或已删除')
    row = srv.store.queue(user.id, row['week_start'], row['week_end'], row['timezone'], refresh=True)
    srv._wake.set()
    return {'id': row['id'], 'status': row['status']}
