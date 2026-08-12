"""Authenticated API adapter for dashboard projections and saved layouts."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from access.context import WorkspaceContext
from access.errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from access.service import AuthorizationService
from shared.errors import APIError

from .dashboard import DashboardLayoutError, DashboardLayoutItem, DashboardService


class DashboardLayoutInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    widget_key: str = Field(min_length=1, max_length=128)
    display_order: int = Field(ge=0, le=9999)
    size: str
    settings: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class DashboardLayoutPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[DashboardLayoutInput] = Field(max_length=100)


def create_dashboard_router(
    service: DashboardService,
    authorization: AuthorizationService,
    require_workspace: Callable[..., WorkspaceContext],
    csrf: Callable[[Request], None],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}/dashboard")

    def require_home(context: WorkspaceContext) -> None:
        try:
            authorization.require(context, app_key="home")
        except WorkspaceNotAvailable as error:
            raise APIError(status_code=404, code="workspace_not_found", message="Resource not found") from error
        except AppNotAvailable as error:
            raise APIError(status_code=404, code="app_not_found", message="Resource not found") from error

    @router.get("")
    def dashboard(context: WorkspaceContext = Depends(require_workspace)) -> dict[str, object]:
        require_home(context)
        layout = service.layout(context)
        return {
            "layout": [asdict(item) for item in layout],
            "widgets": [asdict(item) for item in service.load(context, layout=layout)],
        }

    @router.put("/layout")
    def save_dashboard_layout(
        payload: DashboardLayoutPayload,
        request: Request,
        context: WorkspaceContext = Depends(require_workspace),
    ) -> dict[str, object]:
        require_home(context)
        csrf(request)
        try:
            saved = service.save_layout(
                context,
                tuple(DashboardLayoutItem(**item.model_dump()) for item in payload.items),
            )
        except DashboardLayoutError as error:
            raise APIError(
                status_code=422,
                code="invalid_dashboard_layout",
                message="Dashboard layout is invalid",
            ) from error
        except PermissionDenied as error:
            raise APIError(status_code=403, code="permission_denied", message="Action forbidden") from error
        return {"layout": [asdict(item) for item in saved]}

    return router


__all__ = ["create_dashboard_router"]
