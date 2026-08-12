"""Canonical user-state HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict

from identity.auth import AuthenticationForbidden, AuthenticationRequired, AuthenticationService
from identity.router import _auth_error, _require_same_origin
from identity.workspaces import WorkspaceNotFound, WorkspaceService
from shared.config import AppConfig
from shared.errors import APIError
from workspace_platform.user_state import UserStateNotFound, WorkspaceUserStateService


class ResourceStateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_key: str


class RecentViewRequest(ResourceStateRequest):
    resource_type: str
    resource_id: str


def create_user_state_router(
    state: WorkspaceUserStateService,
    workspaces: WorkspaceService,
    authentication: AuthenticationService,
    config: AppConfig,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}")

    def context(workspace_id: str, request: Request):
        try:
            user = authentication.authenticate(request.cookies.get(config.session.cookie_name))
            return user, workspaces.resolve_context(user.id, workspace_id)
        except AuthenticationRequired as error:
            raise _auth_error(error) from error
        except WorkspaceNotFound as error:
            raise APIError(status_code=404, code="workspace_not_found", message="Resource not found") from error

    def unsafe(request: Request, user) -> None:
        _require_same_origin(request, config.public_origin)
        try:
            authentication.validate_csrf(user.session_id, request.headers.get("x-csrf-token"))
        except AuthenticationForbidden as error:
            raise _auth_error(error) from error

    def call(operation):
        try:
            return operation()
        except UserStateNotFound as error:
            raise APIError(status_code=404, code="resource_not_found", message="Resource not found") from error
        except ValueError as error:
            raise APIError(status_code=422, code="validation_error", message=str(error)) from error

    @router.get("/favorites")
    def favorites(workspace_id: str, request: Request):
        _, resolved = context(workspace_id, request)
        return call(lambda: state.list_favorites(resolved))

    @router.put("/favorites/{resource_type}/{resource_id}", status_code=204)
    def save_favorite(workspace_id: str, resource_type: str, resource_id: str, payload: ResourceStateRequest, request: Request):
        user, resolved = context(workspace_id, request); unsafe(request, user)
        call(lambda: state.set_favorite(resolved, resource_type, resource_id, app_key=payload.app_key))

    @router.delete("/favorites/{resource_type}/{resource_id}", status_code=204)
    def delete_favorite(workspace_id: str, resource_type: str, resource_id: str, request: Request):
        user, resolved = context(workspace_id, request); unsafe(request, user)
        call(lambda: state.clear_favorite(resolved, resource_type, resource_id))

    @router.get("/recent-views")
    def recent_views(workspace_id: str, request: Request):
        _, resolved = context(workspace_id, request)
        return call(lambda: state.list_recent(resolved))

    @router.post("/recent-views", status_code=204)
    def create_recent(workspace_id: str, payload: RecentViewRequest, request: Request):
        user, resolved = context(workspace_id, request); unsafe(request, user)
        call(lambda: state.record_recent(
            resolved, payload.resource_type, payload.resource_id, app_key=payload.app_key
        ))

    @router.put("/recent-views/{resource_type}/{resource_id}", status_code=204)
    def save_recent(workspace_id: str, resource_type: str, resource_id: str, payload: ResourceStateRequest, request: Request):
        user, resolved = context(workspace_id, request); unsafe(request, user)
        call(lambda: state.record_recent(resolved, resource_type, resource_id, app_key=payload.app_key))

    @router.get("/activity")
    def activity(workspace_id: str, request: Request):
        _, resolved = context(workspace_id, request)
        return call(lambda: state.list_activity(resolved))

    return router
