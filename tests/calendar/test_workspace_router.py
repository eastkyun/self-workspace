from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from access.context import PermissionSnapshot, WorkspaceContext
from access.errors import PermissionDenied
from apps.calendar.app.main import (
    create_calendar_router,
    create_legacy_calendar_router,
    initialize_database,
)
from apps.calendar.app.service import CalendarService
from apps.calendar.migrations import MIGRATIONS, TARGET_VERSION
from shared.app_registry import AppRegistry
from shared.migrations import migrate


class Policy:
    permissions = frozenset(
        {
            "calendar.read_all",
            "calendar.create",
            "calendar.update_all",
            "calendar.delete_all",
            "calendar.manage",
        }
    )

    def snapshot(self, context: WorkspaceContext) -> PermissionSnapshot:
        return PermissionSnapshot(
            context.workspace_id,
            context.membership_id,
            context.role_key,
            1,
            (("calendar", "calendar-app"),),
            self.permissions,
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset({"calendar"}),
        )

    def require(self, context, permission_code=None, resource_owner_id=None, *, app_key=None, snapshot=None):
        if permission_code not in self.permissions:
            raise PermissionDenied("permission denied")
        return self.snapshot(context)

    def authorize(self, context, permission_code, resource_owner_id=None, *, snapshot=None):
        return permission_code in self.permissions


def _context(workspace_id: str, user_id: str = "user") -> WorkspaceContext:
    return WorkspaceContext(
        user_id=user_id,
        workspace_id=workspace_id,
        membership_id=f"membership-{workspace_id}",
        role_key="owner",
        enabled_app_ids=("calendar-app",),
        authorization_version=1,
    )


def _client(tmp_path, *, csrf=None):
    path = tmp_path / "calendar.db"
    initialize_database(path, create_backup=False)
    workspace_a, workspace_b, actor = str(uuid4()), str(uuid4()), str(uuid4())
    with sqlite3.connect(path) as connection:
        migrate(
            connection,
            target_version=TARGET_VERSION,
            migrations=MIGRATIONS,
            context={"workspace_id": workspace_a, "actor_id": actor},
        )
    service = CalendarService(
        path,
        authorization=Policy(),  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 8, 6, tzinfo=UTC),
    )

    def require_workspace(workspace_id: str) -> WorkspaceContext:
        return _context(workspace_id)

    application = FastAPI()
    application.include_router(
        create_calendar_router(
            service=service,
            app_registry=AppRegistry.load_default(),
            require_workspace=require_workspace,
            csrf=csrf,
            csrf_cookie_name="workspace_csrf",
        )
    )
    application.include_router(
        create_legacy_calendar_router(
            service=service,
            require_legacy_workspace=lambda request: _context(
                request.headers.get("X-Workspace", workspace_a)
            ),
            csrf=csrf,
        )
    )
    return TestClient(application), workspace_a, workspace_b


def _payload(title: str = "Private event") -> dict[str, object]:
    return {
        "title": title,
        "start_at": "2026-08-06T09:00:00+09:00",
        "due_at": "2026-08-06T10:00:00+09:00",
    }


def test_canonical_router_scopes_tasks_and_topics_by_path_workspace(tmp_path):
    client, workspace_a, workspace_b = _client(tmp_path)
    base_a = f"/api/v1/workspaces/{workspace_a}/calendar"
    base_b = f"/api/v1/workspaces/{workspace_b}/calendar"

    task = client.post(f"{base_a}/tasks", json=_payload()).json()
    topic = client.post(
        f"{base_a}/topics", json={"name": "Private", "color": "#112233"}
    ).json()

    assert client.get(f"{base_b}/tasks").json() == []
    assert client.get(f"{base_b}/topics").json() == []
    assert client.patch(
        f"{base_b}/tasks/{task['id']}", json={"title": "stolen"}
    ).status_code == 404
    assert client.patch(
        f"{base_b}/topics/{topic['id']}", json={"name": "stolen"}
    ).status_code == 404
    assert client.get(f"{base_a}/tasks").json()[0]["title"] == "Private event"


def test_canonical_ui_exports_api_csrf_and_static_configuration(tmp_path):
    client, workspace_a, _ = _client(tmp_path)

    response = client.get(f"/w/{workspace_a}/calendar")

    assert response.status_code == 200
    assert f'data-api-base="/api/v1/workspaces/{workspace_a}/calendar"' in response.text
    assert f'data-internal-link-endpoint="/api/v1/workspaces/{workspace_a}/internal-links/resolve"' in response.text
    assert f'data-notes-api-base="/api/v1/workspaces/{workspace_a}/notes"' in response.text
    assert 'data-csrf-cookie-name="workspace_csrf"' in response.text
    assert f'href="/w/{workspace_a}/notes/"' in response.text
    assert 'href="/"' in response.text
    assert 'src="/apps/calendar/static/app.js"' in response.text


def test_mutations_run_csrf_hook_and_legacy_adapter_resolves_request_workspace(tmp_path):
    def csrf(request: Request) -> None:
        if request.headers.get("X-CSRF-Token") != "valid":
            raise HTTPException(status_code=403, detail="CSRF failed")

    client, workspace_a, workspace_b = _client(tmp_path, csrf=csrf)
    canonical = f"/api/v1/workspaces/{workspace_a}/calendar/tasks"
    assert client.post(canonical, json=_payload()).status_code == 403
    assert client.post(
        canonical, json=_payload(), headers={"X-CSRF-Token": "valid"}
    ).status_code == 201

    redirect = client.get(
        "/apps/calendar", headers={"X-Workspace": workspace_b}, follow_redirects=False
    )
    assert redirect.status_code == 307
    assert redirect.headers["location"] == f"/w/{workspace_b}/calendar"
    legacy = client.get(
        "/apps/calendar/api/tasks", headers={"X-Workspace": workspace_b}
    )
    assert legacy.status_code == 200
    assert legacy.json() == []
    assert legacy.headers["deprecation"] == "true"
    assert workspace_b in legacy.headers["link"]
