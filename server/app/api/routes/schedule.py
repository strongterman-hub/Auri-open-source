from datetime import date
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from app.api.dependencies import ContainerDep, CurrentUserDep
from app.schedule.models import CreateRequest, UpdateRequest
from app.schedule.service import ScheduleError

router = APIRouter(prefix="/schedule", tags=["schedule"])

def invoke(fn, *args):
    try:
        return fn(*args)
    except ScheduleError as error:
        raise HTTPException(error.code, detail=error.details) from error
    except (ValidationError, ValueError) as error:
        raise HTTPException(422, detail={"message": str(error)}) from error

@router.get("")
def list_events(start: date, end: date, container: ContainerDep, current_user: CurrentUserDep):
    return invoke(container.schedule_service.list, current_user.id, "default", start, end)

@router.get("/{event_id}")
def get_event(event_id: str, container: ContainerDep, current_user: CurrentUserDep):
    return invoke(container.schedule_service.get, current_user.id, "default", event_id)

@router.post("", status_code=201)
def create_event(payload: CreateRequest, container: ContainerDep, current_user: CurrentUserDep):
    return invoke(container.schedule_service.create, current_user.id, "default", payload)

@router.patch("/{event_id}")
def update_event(event_id: str, payload: UpdateRequest, container: ContainerDep, current_user: CurrentUserDep):
    return invoke(container.schedule_service.update, current_user.id, "default", event_id, payload)
