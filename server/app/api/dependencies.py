from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from app.auth.models import AuthUser
from app.state.container import Container


def get_container(request: Request) -> Container:
    return request.app.state.container


ContainerDep = Annotated[Container, Depends(get_container)]


def get_current_user(
    container: ContainerDep,
    authorization: Annotated[str | None, Header()] = None,
) -> AuthUser:
    token = (authorization or "").removeprefix("Bearer ").strip()
    user = container.auth_service.get_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return user


CurrentUserDep = Annotated[AuthUser, Depends(get_current_user)]
