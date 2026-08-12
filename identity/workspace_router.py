"""Versioned canonical Workspace and membership HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from identity.auth import AuthenticationForbidden, AuthenticationRequired, AuthenticationService
from identity.api_v1_schemas import (
    AppOrderRequest,
    AppPolicyResponse,
    AppUpdateRequest,
    InvitationRequest,
    OwnershipTransferRequest,
    PermissionOverrideRequest,
    RoleChangeRequest,
    RolePolicyResponse,
    TeamCreateRequest,
    WorkspaceUpdateRequest,
)
from identity.router import _auth_error, _require_same_origin
from identity.workspaces import (
    WorkspaceConflict,
    WorkspaceForbidden,
    WorkspaceNotFound,
    WorkspaceService,
)
from shared.config import AppConfig
from shared.errors import APIError
from workspace_platform.app_policy import (
    AppPolicyConflict,
    AppPolicyForbidden,
    AppPolicyNotFound,
    WorkspaceAppPolicyService,
)


def _workspace_error(error: Exception) -> APIError:
    if isinstance(error, WorkspaceNotFound):
        return APIError(status_code=404, code="workspace_not_found", message="Resource not found")
    if isinstance(error, WorkspaceForbidden):
        return APIError(status_code=403, code="workspace_forbidden", message="Action forbidden")
    if isinstance(error, WorkspaceConflict):
        return APIError(status_code=409, code="workspace_conflict", message=str(error))
    return APIError(status_code=422, code="validation_error", message="Request validation failed")


def create_workspace_router(
    service: WorkspaceService,
    authentication: AuthenticationService,
    config: AppConfig,
    app_policy: WorkspaceAppPolicyService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")
    cookie_name = config.session.cookie_name

    def user(request: Request):
        try:
            return authentication.authenticate(request.cookies.get(cookie_name))
        except AuthenticationRequired as error:
            raise _auth_error(error) from error

    def unsafe(request: Request, authenticated_user) -> None:
        _require_same_origin(request, config.public_origin)
        try:
            authentication.validate_csrf(
                authenticated_user.session_id, request.headers.get("x-csrf-token")
            )
        except AuthenticationForbidden as error:
            raise _auth_error(error) from error

    def call(operation):
        try:
            return operation()
        except (WorkspaceNotFound, WorkspaceForbidden, WorkspaceConflict, ValueError) as error:
            raise _workspace_error(error) from error

    def app_call(operation):
        try:
            return operation()
        except AppPolicyNotFound as error:
            raise APIError(status_code=404, code="app_not_found", message="Resource not found") from error
        except AppPolicyForbidden as error:
            raise APIError(status_code=403, code="permission_denied", message="Action forbidden") from error
        except AppPolicyConflict as error:
            raise APIError(status_code=409, code="app_conflict", message=str(error)) from error
        except ValueError as error:
            raise APIError(status_code=422, code="validation_error", message="Request validation failed") from error

    @router.get("/workspaces")
    def list_workspaces(request: Request):
        return service.list_workspaces(user(request))

    @router.post("/workspaces", status_code=201)
    def create_workspace(payload: TeamCreateRequest, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        return call(lambda: service.create_team(authenticated, payload.name, payload.description, payload.timezone))

    @router.get("/workspaces/{workspace_id}")
    def get_workspace(workspace_id: str, request: Request):
        authenticated = user(request)
        return call(lambda: service.get_workspace(authenticated, workspace_id))

    @router.patch("/workspaces/{workspace_id}")
    def update_workspace(workspace_id: str, payload: WorkspaceUpdateRequest, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        return call(lambda: service.update_workspace(authenticated, workspace_id, name=payload.name, description=payload.description, timezone_name=payload.timezone))

    @router.delete("/workspaces/{workspace_id}", status_code=204)
    def delete_workspace(workspace_id: str, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        call(lambda: service.delete_workspace(authenticated, workspace_id))

    @router.post("/workspaces/{workspace_id}/select", status_code=204)
    def select_workspace(workspace_id: str, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        call(lambda: service.select_workspace(authenticated, workspace_id))

    @router.post("/workspaces/{workspace_id}/transfer-ownership", status_code=204)
    def transfer_ownership(workspace_id: str, payload: OwnershipTransferRequest, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        call(lambda: service.transfer_ownership(authenticated, workspace_id, payload.member_id))

    @router.post("/workspaces/{workspace_id}/leave", status_code=204)
    def leave_workspace(workspace_id: str, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        call(lambda: service.leave_workspace(authenticated, workspace_id))

    @router.get("/workspaces/{workspace_id}/members")
    def list_members(workspace_id: str, request: Request):
        authenticated = user(request)
        return call(lambda: service.list_members(authenticated, workspace_id))

    @router.post("/workspaces/{workspace_id}/invitations", status_code=201)
    def create_invitation(workspace_id: str, payload: InvitationRequest, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        result = call(lambda: service.invite(authenticated, workspace_id, payload.email, payload.role_key))
        return {"id": result.invitation_id, "delivery_status": result.delivery_status}

    @router.get("/invitations/{token}")
    def inspect_invitation(token: str, request: Request):
        authenticated = user(request)
        return call(lambda: service.inspect_invitation(authenticated, token))

    @router.post("/invitations/{token}/accept", status_code=201)
    def accept_invitation(token: str, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        return call(lambda: service.accept_invitation(authenticated, token))

    @router.post("/invitations/{token}/reject", status_code=204)
    def reject_invitation(token: str, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        call(lambda: service.reject_invitation(authenticated, token))

    @router.post("/workspaces/{workspace_id}/invitations/{invitation_id}/resend", status_code=201)
    def resend_invitation(workspace_id: str, invitation_id: str, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        result = call(lambda: service.resend_invitation(authenticated, workspace_id, invitation_id))
        return {"id": result.invitation_id, "delivery_status": result.delivery_status}

    @router.delete("/workspaces/{workspace_id}/invitations/{invitation_id}", status_code=204)
    def cancel_invitation(workspace_id: str, invitation_id: str, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        call(lambda: service.cancel_invitation(authenticated, workspace_id, invitation_id))

    @router.patch("/workspaces/{workspace_id}/members/{member_id}/role", status_code=204)
    def change_member_role(workspace_id: str, member_id: str, payload: RoleChangeRequest, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        call(lambda: service.change_member_role(authenticated, workspace_id, member_id, payload.role_key))

    @router.delete("/workspaces/{workspace_id}/members/{member_id}", status_code=204)
    def remove_member(workspace_id: str, member_id: str, request: Request):
        authenticated = user(request); unsafe(request, authenticated)
        call(lambda: service.remove_member(authenticated, workspace_id, member_id))

    if app_policy is not None:
        @router.get("/workspaces/{workspace_id}/apps", response_model=list[AppPolicyResponse])
        def list_workspace_apps(workspace_id: str, request: Request):
            authenticated = user(request)
            context = call(lambda: service.resolve_context(authenticated.id, workspace_id))
            return app_call(lambda: app_policy.list_apps(context))

        @router.get("/workspaces/{workspace_id}/apps/{app_key}/access", status_code=204)
        def access_workspace_app(workspace_id: str, app_key: str, request: Request):
            authenticated = user(request)
            context = call(lambda: service.resolve_context(authenticated.id, workspace_id))
            app_call(lambda: app_policy.require_app(context, app_key))

        @router.patch("/workspaces/{workspace_id}/apps/order", status_code=204)
        def order_workspace_apps(workspace_id: str, payload: AppOrderRequest, request: Request):
            authenticated = user(request); unsafe(request, authenticated)
            app_call(lambda: app_policy.reorder(authenticated.id, workspace_id, payload.app_keys))

        @router.patch("/workspaces/{workspace_id}/apps/{app_key}", status_code=204)
        def update_workspace_app(workspace_id: str, app_key: str, payload: AppUpdateRequest, request: Request):
            authenticated = user(request); unsafe(request, authenticated)
            app_call(lambda: app_policy.update_app(authenticated.id, workspace_id, app_key, enabled=payload.enabled, display_order=payload.display_order, settings=payload.settings))

        @router.get("/workspaces/{workspace_id}/roles", response_model=list[RolePolicyResponse])
        def list_workspace_roles(workspace_id: str, request: Request):
            authenticated = user(request)
            context = call(lambda: service.resolve_context(authenticated.id, workspace_id))
            return app_call(lambda: app_policy.role_matrix(context))

        @router.get("/workspaces/{workspace_id}/permissions")
        def list_permission_overrides(workspace_id: str, request: Request):
            authenticated = user(request)
            context = call(lambda: service.resolve_context(authenticated.id, workspace_id))
            return app_call(lambda: app_policy.permission_override_matrix(context))

        @router.put(
            "/workspaces/{workspace_id}/members/{member_id}/permissions/{permission_code}",
            status_code=204,
        )
        def set_permission_override(
            workspace_id: str,
            member_id: str,
            permission_code: str,
            payload: PermissionOverrideRequest,
            request: Request,
        ):
            authenticated = user(request); unsafe(request, authenticated)
            app_call(lambda: app_policy.set_permission_override(
                authenticated.id, workspace_id, member_id, permission_code, payload.effect
            ))

        @router.delete(
            "/workspaces/{workspace_id}/members/{member_id}/permissions/{permission_code}",
            status_code=204,
        )
        def clear_permission_override(
            workspace_id: str, member_id: str, permission_code: str, request: Request
        ):
            authenticated = user(request); unsafe(request, authenticated)
            app_call(lambda: app_policy.clear_permission_override(
                authenticated.id, workspace_id, member_id, permission_code
            ))

    return router
