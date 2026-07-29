from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from shared.app_registry import AppRegistry
from shared.templating import create_templates, shell_context, workspace_path


def test_shell_context_selects_current_app() -> None:
    app = FastAPI(root_path="/workspace")

    @app.get("/")
    def index(request: Request) -> dict:
        context = shell_context(
            request,
            AppRegistry.load_default(),
            current_app_id="calendar",
            page_title="Calendar",
        )
        return {
            "current_app_id": context["current_app_id"],
            "current_app_name": context["current_app_name"],
            "navigation_app_ids": [
                item.id for item in context["navigation_apps"]
            ],
            "launcher_app_ids": [item.id for item in context["launcher_apps"]],
        }

    with TestClient(app) as client:
        payload = client.get("/").json()

    assert payload["current_app_id"] == "calendar"
    assert payload["current_app_name"] == "Calendar"
    assert payload["navigation_app_ids"] == ["calendar", "notes"]
    assert payload["launcher_app_ids"] == ["calendar", "notes"]


def test_workspace_path_uses_external_root_path() -> None:
    app = FastAPI(root_path="/workspace")

    @app.get("/")
    def index(request: Request) -> dict[str, str]:
        return {"path": workspace_path(request, "/apps/calendar/")}

    with TestClient(app) as client:
        assert client.get("/").json() == {"path": "/workspace/apps/calendar/"}


def test_shared_shell_renders_navigation_and_content(tmp_path: Path) -> None:
    (tmp_path / "page.html").write_text(
        '{% extends "app_shell.html" %}{% block content %}<div id="content"></div>{% endblock %}',
        encoding="utf-8",
    )
    templates = create_templates(tmp_path)
    app = FastAPI()

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(
            request,
            "page.html",
            shell_context(
                request,
                AppRegistry.load_default(),
                current_app_id="calendar",
                page_title="Calendar",
            ),
        )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'id="sidebar"' in response.text
    assert 'class="workspace-header"' in response.text
    assert response.text.count('data-app-id="calendar"') == 2
    assert 'aria-current="page"' in response.text
    assert 'id="content"' in response.text
    assert "<iframe" not in response.text


def test_workspace_path_rejects_relative_paths() -> None:
    app = FastAPI()

    @app.get("/")
    def index(request: Request):
        with pytest.raises(ValueError, match="start with"):
            workspace_path(request, "apps/calendar/")
        return {}

    with TestClient(app) as client:
        assert client.get("/").status_code == 200
