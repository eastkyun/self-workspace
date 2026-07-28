from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from shared.app_registry import HubApp, navigation_apps

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
    apps: Sequence[HubApp],
    *,
    current_app_id: str,
    page_title: str,
) -> dict[str, Any]:
    registered_apps = list(apps)
    current_app = next(
        (app for app in registered_apps if app.id == current_app_id),
        None,
    )
    path_for: Callable[[str], str] = lambda path: workspace_path(request, path)
    return {
        "apps": registered_apps,
        "navigation_apps": navigation_apps(registered_apps),
        "current_app_id": current_app_id,
        "current_app_name": current_app.name if current_app else "App Hub",
        "page_title": page_title,
        "workspace_path": path_for,
    }
