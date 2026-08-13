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
    assert payload["navigation_app_ids"] == ["calendar", "notes", "publishing"]
    assert payload["launcher_app_ids"] == ["calendar", "notes", "publishing"]


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
    assert 'id="workspace-guest-actions"' in response.text
    assert 'id="workspace-account-menu"' in response.text
    assert 'id="workspace-profile-dialog"' not in response.text
    assert 'id="workspace-logout"' in response.text
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


def test_account_visibility_css_respects_hidden_state() -> None:
    stylesheet = Path("shared/static/shell.css").read_text(encoding="utf-8")

    assert ".workspace-account-actions[hidden]" in stylesheet
    assert ".workspace-account[hidden]" in stylesheet


def test_header_popovers_and_modal_use_a_predictable_layer_order() -> None:
    stylesheet = Path("shared/static/shell.css").read_text(encoding="utf-8")

    assert "--workspace-layer-header: 10" in stylesheet
    assert "--workspace-layer-scrim: 20" in stylesheet
    assert "--workspace-layer-sidebar: 30" in stylesheet
    assert "z-index: var(--workspace-layer-header)" in stylesheet
    assert ".workspace-profile-dialog::backdrop" not in stylesheet


def test_shared_shell_provides_an_accessible_reduced_motion_loader() -> None:
    template = Path("shared/templates/app_shell.html").read_text(encoding="utf-8")
    stylesheet = Path("shared/static/shell.css").read_text(encoding="utf-8")
    script = Path("shared/static/shell.js").read_text(encoding="utf-8")

    assert 'id="workspace-app-loading"' in template
    assert 'role="status"' in template
    assert 'aria-busy="true"' in template
    assert "workspace-loading-spinner" in stylesheet
    assert "prefers-reduced-motion: reduce" in stylesheet
    assert "workspaceAppReady" in script
    assert "data-workspace-await-ready" in script
