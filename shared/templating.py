from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from shared.app_registry import AppRegistry

SHARED_DIR = Path(__file__).resolve().parent
SHARED_TEMPLATES_DIR = SHARED_DIR / "templates"
SHARED_STATIC_DIR = SHARED_DIR / "static"


def create_templates(app_templates_dir: Path) -> Jinja2Templates:
    return Jinja2Templates(directory=[app_templates_dir, SHARED_TEMPLATES_DIR])


def workspace_path(request: Request, path: str) -> str:
    if not path.startswith("/"):
        raise ValueError("workspace paths must start with /")
    prefix = request.scope.get(
        "app_root_path",
        request.scope.get("root_path", ""),
    ).rstrip("/")
    return f"{prefix}{path}"


def shell_context(
    request: Request,
    registry: AppRegistry,
    *,
    current_app_id: str,
    page_title: str,
    path_for: Callable[[str], str] | None = None,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    registered_apps = registry.get_enabled()
    navigation_apps = registry.get_navigation()
    launcher_apps = registry.get_launcher()
    current_app = registry.get_app(current_app_id)
    if path_for is None:
        path_for = lambda path: workspace_path(request, path)
    return {
        "apps": registered_apps,
        "navigation_apps": navigation_apps,
        "launcher_apps": launcher_apps,
        "current_app_id": current_app_id,
        "current_app_name": current_app.display_name if current_app else "App Hub",
        "page_title": page_title,
        "workspace_path": path_for,
        "workspace_id": workspace_id,
    }


def project_workspace_shell_apps(
    effective_apps: Iterable[object], workspace_id: str
) -> dict[str, Any]:
    """Project one effective-app snapshot into every shared menu surface."""
    apps = tuple(effective_apps)

    def project(app: object, *, launcher: bool = False) -> dict[str, Any]:
        key = str(getattr(app, "key"))
        route = str(getattr(app, "route")).replace("{workspace_id}", workspace_id)
        return {
            "id": key,
            "kind": "internal",
            "route": route,
            "icon": str(getattr(app, "icon")),
            "display_name": str(getattr(app, "display_name")),
            "default_pinned": key in {"notes", "calendar"} if launcher else False,
        }

    return {
        "apps": apps,
        "navigation_apps": [
            project(app) for app in apps if bool(getattr(app, "sidebar_visible"))
        ],
        "launcher_apps": [
            project(app, launcher=True)
            for app in apps
            if bool(getattr(app, "launcher_visible"))
        ],
    }


def workspace_shell_context(
    request: Request,
    registry: AppRegistry,
    *,
    current_app_id: str,
    page_title: str,
    workspace_id: str,
    effective_apps: Iterable[object],
    path_for: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Build the shared shell from one already-authorized Workspace app view."""
    context = shell_context(
        request,
        registry,
        current_app_id=current_app_id,
        page_title=page_title,
        path_for=path_for,
        workspace_id=workspace_id,
    )
    context.update(project_workspace_shell_apps(effective_apps, workspace_id))
    return context


def authorized_workspace_shell_context(
    request: Request,
    registry: AppRegistry,
    *,
    current_app_id: str,
    page_title: str,
    workspace_context: object,
    effective_apps: object,
    path_for: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Resolve and project the effective app snapshot for any Workspace app."""
    authorization = getattr(effective_apps, "authorization")
    snapshot = authorization.permission_snapshot(workspace_context)
    apps = effective_apps.list(workspace_context, snapshot=snapshot)
    return workspace_shell_context(
        request,
        registry,
        current_app_id=current_app_id,
        page_title=page_title,
        workspace_id=str(getattr(workspace_context, "workspace_id")),
        effective_apps=apps,
        path_for=path_for,
    )
