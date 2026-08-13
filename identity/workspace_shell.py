"""Authenticated Workspace selector and Phase 3 settings shell."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from identity.auth import AuthenticationRequired, AuthenticationService
from identity.router import _auth_error
from identity.workspaces import WorkspaceConflict, WorkspaceNotFound, WorkspaceService
from shared.config import AppConfig
from shared.errors import APIError
from shared.templating import create_templates
from workspace_platform.app_policy import (
    AppPolicyForbidden,
    AppPolicyNotFound,
    WorkspaceAppPolicyService,
)
from workspace_platform.dashboard import DashboardService
from workspace_platform.effective_apps import EffectiveWorkspaceApps
from workspace_platform.user_state import WorkspaceUserStateService


templates = create_templates(Path(__file__).resolve().parent / "templates")


def create_workspace_shell_router(
    service: WorkspaceService,
    authentication: AuthenticationService,
    config: AppConfig,
    app_policy: WorkspaceAppPolicyService,
    effective_apps: EffectiveWorkspaceApps,
    user_state: WorkspaceUserStateService | None = None,
    dashboard: DashboardService | None = None,
) -> APIRouter:
    router = APIRouter()

    def current_user(request: Request):
        try:
            return authentication.authenticate(request.cookies.get(config.session.cookie_name))
        except AuthenticationRequired as error:
            raise _auth_error(error) from error

    def shell_context(
        request: Request,
        user,
        workspace_id: str,
        *,
        include_dashboard: bool = False,
    ) -> dict[str, object]:
        try:
            workspace = service.get_workspace(user, workspace_id)
            workspaces = service.list_workspaces(user)
        except WorkspaceNotFound as error:
            raise APIError(status_code=404, code="workspace_not_found", message="Resource not found") from error
        context = {
            "request": request,
            "workspace": workspace,
            "workspace_id": workspace_id,
            "workspaces": workspaces,
            "user_id": user.id,
            "session_cookie_name": config.session.cookie_name,
            "pin_import_eligible": workspace["type"] == "PERSONAL" and next(
                (item["id"] for item in workspaces if item["type"] == "PERSONAL"), None
            ) == workspace_id,
            "workspace_creation_counts": service.creation_counts(user),
            "workspace_creation_limit": 5,
        }
        resolved = service.resolve_context(user.id, workspace_id)
        snapshot = effective_apps.authorization.permission_snapshot(resolved)
        context["workspace_context"] = resolved
        context["permission_snapshot"] = snapshot
        context["effective_apps"] = effective_apps.list(resolved, snapshot=snapshot)
        app_items = context["effective_apps"]
        canonical_routes = {
            "/": f"/w/{workspace_id}/home",
            "/apps/notes/": f"/w/{workspace_id}/notes",
            "/apps/calendar/": f"/w/{workspace_id}/calendar",
            "/apps/publishing/": f"/w/{workspace_id}/publications",
        }
        context.update(
            {
                "apps": app_items,
                "navigation_apps": [
                    {
                        "id": app.key, "kind": "internal", "route": app.route.replace("{workspace_id}", workspace_id),
                        "icon": app.icon, "display_name": app.display_name,
                    }
                    for app in app_items if app.sidebar_visible
                ],
                "launcher_apps": [
                    {
                        "id": app.key, "kind": "internal", "route": app.route.replace("{workspace_id}", workspace_id),
                        "icon": app.icon, "display_name": app.display_name,
                        "default_pinned": app.key in {"notes", "calendar"},
                    }
                    for app in app_items if app.launcher_visible
                ],
                "current_app_id": "home",
                "current_app_name": workspace["name"],
                "page_title": workspace["name"],
                "workspace_path": lambda path: canonical_routes.get(path, path),
                "effective_app_map": {app.key: app for app in app_items},
                "home_apps": [
                    app for app in app_items
                    if app.home_card_visible or app.launcher_visible
                ],
            }
        )
        context["can_manage_settings"] = effective_apps.authorization.authorize(resolved, "workspace.settings.manage", snapshot=snapshot)
        context["can_manage_apps"] = effective_apps.authorization.authorize(resolved, "workspace.apps.manage", snapshot=snapshot)
        context["can_invite_members"] = effective_apps.authorization.authorize(resolved, "workspace.members.invite", snapshot=snapshot)
        context["can_assign_roles"] = effective_apps.authorization.authorize(resolved, "workspace.roles.assign", snapshot=snapshot)
        context["can_manage_permissions"] = effective_apps.authorization.authorize(resolved, "workspace.permissions.manage", snapshot=snapshot)
        if include_dashboard and dashboard is not None:
            layout = dashboard.layout(resolved)
            context["dashboard_layout"] = layout
            context["dashboard_widgets"] = dashboard.load(resolved, layout=layout)
        return context

    settings_metadata = {
        "profile": ("내 프로필", "계정 이름과 이메일 정보를 관리합니다."),
        "general": ("일반 설정", "워크스페이스의 기본 정보와 시간대 기준을 관리합니다."),
        "members": ("멤버", "워크스페이스에 참여한 멤버를 확인하고 새 멤버를 초대합니다."),
        "apps": ("앱", "워크스페이스에서 사용할 앱과 표시 상태를 관리합니다."),
        "roles": ("역할", "기본 역할에 부여된 권한을 확인합니다."),
        "permissions": ("권한", "멤버별 권한 예외를 안전하게 관리합니다."),
    }

    def settings_context(request: Request, user, workspace_id: str, section: str) -> dict[str, object]:
        context = shell_context(request, user, workspace_id)
        heading, description = settings_metadata[section]
        context.update(
            {
                "current_app_id": "workspace",
                "current_app_name": "워크스페이스 설정",
                "page_title": heading,
                "settings_heading": heading,
                "settings_description": description,
                "settings_section": section,
            }
        )
        return context

    @router.get("/workspace-selector", response_class=HTMLResponse, include_in_schema=False)
    def selector(request: Request):
        user = current_user(request)
        workspaces = service.list_workspaces(user)
        if not workspaces:
            raise APIError(status_code=404, code="workspace_not_found", message="Resource not found")
        selected = next((item for item in workspaces if item["id"] == user.last_workspace_id), workspaces[0])
        return RedirectResponse(f"/w/{selected['id']}/home", status_code=303)

    @router.get("/w/{workspace_id}/home", response_class=HTMLResponse, include_in_schema=False)
    def workspace_home(workspace_id: str, request: Request):
        user = current_user(request)
        return templates.TemplateResponse(
            request,
            "workspace_home.html",
            shell_context(request, user, workspace_id, include_dashboard=True),
        )

    @router.get("/w/{workspace_id}/settings", include_in_schema=False)
    def settings_root(workspace_id: str, request: Request):
        user = current_user(request)
        context = shell_context(request, user, workspace_id)
        section = "general" if context["can_manage_settings"] else "members"
        return RedirectResponse(
            f"/w/{workspace_id}/settings/{section}", status_code=303
        )

    legacy_app_routes = {
        "notes": "/apps/notes/",
        "calendar": "/apps/calendar/",
        "publications": "/publications/",
    }

    @router.get("/w/{workspace_id}/{app_slug}", include_in_schema=False)
    def legacy_app_adapter(workspace_id: str, app_slug: str, request: Request):
        target = legacy_app_routes.get(app_slug)
        if target is None and app_slug != "search":
            raise APIError(status_code=404, code="app_not_found", message="Resource not found")
        user = current_user(request)
        try:
            resolved = service.resolve_context(user.id, workspace_id)
        except WorkspaceNotFound as error:
            raise APIError(
                status_code=404,
                code="workspace_not_found",
                message="Resource not found",
            ) from error
        app_key = "publishing" if app_slug == "publications" else app_slug
        try:
            app_policy.require_app(resolved, app_key)
        except AppPolicyNotFound as error:
            raise APIError(status_code=404, code="app_not_found", message="Resource not found") from error
        if app_slug == "search":
            context = shell_context(request, user, workspace_id)
            context["current_app_id"] = "search"
            context["current_app_name"] = "Search"
            context["page_title"] = "Workspace Search"
            return templates.TemplateResponse(request, "workspace_search.html", context)
        return RedirectResponse(target, status_code=307)

    @router.get("/w/{workspace_id}/settings/general", response_class=HTMLResponse, include_in_schema=False)
    def general_settings(workspace_id: str, request: Request):
        user = current_user(request)
        context = settings_context(request, user, workspace_id, "general")
        if not context["can_manage_settings"]:
            raise APIError(status_code=403, code="permission_denied", message="Action forbidden")
        return templates.TemplateResponse(request, "workspace_general.html", context)

    @router.get("/w/{workspace_id}/settings/profile", response_class=HTMLResponse, include_in_schema=False)
    def profile_settings(workspace_id: str, request: Request):
        user = current_user(request)
        context = settings_context(request, user, workspace_id, "profile")
        context["profile"] = {"display_name": user.display_name, "email": user.email}
        return templates.TemplateResponse(request, "workspace_profile.html", context)

    @router.get("/w/{workspace_id}/settings/members", response_class=HTMLResponse, include_in_schema=False)
    def member_settings(workspace_id: str, request: Request):
        user = current_user(request)
        context = settings_context(request, user, workspace_id, "members")
        context["members"] = service.list_members(user, workspace_id)
        return templates.TemplateResponse(request, "workspace_members.html", context)

    @router.get("/w/{workspace_id}/settings/apps", response_class=HTMLResponse, include_in_schema=False)
    def app_settings(workspace_id: str, request: Request):
        user = current_user(request)
        context = settings_context(request, user, workspace_id, "apps")
        if not context["can_manage_apps"]:
            raise APIError(status_code=403, code="permission_denied", message="Action forbidden")
        context["app_policies"] = app_policy.list_apps(
            context["workspace_context"], snapshot=context["permission_snapshot"]
        )
        return templates.TemplateResponse(request, "workspace_apps.html", context)

    @router.get("/w/{workspace_id}/settings/roles", response_class=HTMLResponse, include_in_schema=False)
    def role_settings(workspace_id: str, request: Request):
        user = current_user(request)
        context = settings_context(request, user, workspace_id, "roles")
        try:
            context["roles"] = app_policy.role_matrix(context["workspace_context"])
        except AppPolicyForbidden as error:
            raise APIError(status_code=403, code="permission_denied", message="Action forbidden") from error
        return templates.TemplateResponse(request, "workspace_roles.html", context)

    @router.get("/w/{workspace_id}/settings/permissions", response_class=HTMLResponse, include_in_schema=False)
    def permission_settings(workspace_id: str, request: Request):
        user = current_user(request)
        context = settings_context(request, user, workspace_id, "permissions")
        if not context["can_manage_permissions"]:
            raise APIError(status_code=403, code="permission_denied", message="Action forbidden")
        try:
            context["permission_matrix"] = app_policy.permission_override_matrix(
                context["workspace_context"]
            )
        except AppPolicyForbidden as error:
            raise APIError(status_code=403, code="permission_denied", message="Action forbidden") from error
        return templates.TemplateResponse(request, "workspace_permissions.html", context)

    @router.get("/invitations/{token}", response_class=HTMLResponse, include_in_schema=False)
    def invitation_page(token: str, request: Request):
        user = current_user(request)
        try:
            invitation = service.inspect_invitation(user, token)
        except WorkspaceNotFound as error:
            raise APIError(status_code=404, code="invitation_not_found", message="Resource not found") from error
        except WorkspaceConflict as error:
            raise APIError(status_code=409, code="invitation_conflict", message=str(error)) from error
        return templates.TemplateResponse(
            request,
            "workspace_invitation.html",
            {
                "request": request,
                "invitation": invitation,
                "invitation_token": token,
                "session_cookie_name": config.session.cookie_name,
            },
        )

    @router.get("/invitations", response_class=HTMLResponse, include_in_schema=False)
    def received_invitations_page(request: Request):
        current_user(request)
        return templates.TemplateResponse(request, "workspace_invitations.html", {"request": request, "session_cookie_name": config.session.cookie_name})

    return router
