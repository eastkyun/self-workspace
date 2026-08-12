"""FastAPI adapters around the shared AuthorizationService."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Request

from identity.auth import AuthenticationRequired, AuthenticationService
from shared.errors import APIError

from .context import WorkspaceContext
from .errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from .service import AuthorizationService


def workspace_dependency(
    authentication: AuthenticationService,
    authorization: AuthorizationService,
    cookie_name: str,
) -> Callable[[str, Request], WorkspaceContext]:
    def require_workspace(workspace_id: str, request: Request) -> WorkspaceContext:
        try:
            user = authentication.authenticate(request.cookies.get(cookie_name))
        except AuthenticationRequired as error:
            raise APIError(status_code=401, code="authentication_required", message="Authentication required") from error
        try:
            return authorization.resolve_context(user.id, workspace_id)
        except WorkspaceNotAvailable as error:
            raise APIError(status_code=404, code="workspace_not_found", message="Resource not found") from error

    return require_workspace


def require_app(
    authorization: AuthorizationService, app_key: str
) -> Callable[[WorkspaceContext], WorkspaceContext]:
    def dependency(context: WorkspaceContext) -> WorkspaceContext:
        try:
            authorization.require(context, app_key=app_key)
        except (AppNotAvailable, WorkspaceNotAvailable) as error:
            raise APIError(status_code=404, code="app_not_found", message="Resource not found") from error
        return context

    return dependency


def require_permission(
    authorization: AuthorizationService, permission_code: str
) -> Callable[[WorkspaceContext], WorkspaceContext]:
    def dependency(context: WorkspaceContext) -> WorkspaceContext:
        try:
            authorization.require(context, permission_code)
        except WorkspaceNotAvailable as error:
            raise APIError(status_code=404, code="workspace_not_found", message="Resource not found") from error
        except PermissionDenied as error:
            raise APIError(status_code=403, code="permission_denied", message="Action forbidden") from error
        return context

    return dependency
