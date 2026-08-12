from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from portal.main import create_app


def test_workspace_search_requires_authentication_and_returns_only_scoped_results(tmp_path):
    calendar_path = tmp_path / "calendar.db"
    notes_path = tmp_path / "notes.db"
    application = create_app(calendar_path, notes_database_path=notes_path)
    origin = {"Origin": "http://testserver"}

    with TestClient(application) as client:
        assert client.get("/api/v1/workspaces/missing/search", params={"q": "roadmap"}).status_code == 401
        signup = client.post(
            "/api/v1/auth/signup",
            headers=origin,
            json={"email": "search-owner@example.com", "password": "password"},
        )
        workspace_id = signup.json()["workspace_id"]
        user_id = signup.json()["user_id"]
        with sqlite3.connect(notes_path) as connection:
            connection.execute(
                """INSERT INTO notes (
                    title, content, note_type, is_pinned, created_at, updated_at,
                    workspace_id, created_by, updated_by, deleted_at
                ) VALUES ('Roadmap note', 'Phase nine details', 'general', 0,
                          '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z', ?, ?, ?, NULL)""",
                (workspace_id, user_id, user_id),
            )
            connection.execute(
                """INSERT INTO notes (
                    title, content, note_type, is_pinned, created_at, updated_at,
                    workspace_id, created_by, updated_by, deleted_at
                ) VALUES ('Deleted roadmap', 'must stay hidden', 'general', 0,
                          '2026-08-10T00:00:00Z', '2026-08-10T00:00:00Z', ?, ?, ?,
                          '2026-08-10T01:00:00Z')""",
                (workspace_id, user_id, user_id),
            )

        page = client.get(f"/w/{workspace_id}/search")
        response = client.get(
            f"/api/v1/workspaces/{workspace_id}/search",
            params={"q": "roadmap"},
        )

    assert page.status_code == 200
    assert "필요한 작업을 한 번에 찾으세요" in page.text
    assert response.status_code == 200
    payload = response.json()
    notes = next(group for group in payload["groups"] if group["app_key"] == "notes")
    assert [item["title"] for item in notes["results"]] == ["Roadmap note"]
    assert all(item["workspace_id"] == workspace_id for item in notes["results"])


def test_dashboard_projects_manifest_widgets_and_persists_layout(tmp_path):
    application = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
    )
    origin = {"Origin": "http://testserver"}

    with TestClient(application) as client:
        signup = client.post(
            "/api/v1/auth/signup",
            headers=origin,
            json={"email": "dashboard-owner@example.com", "password": "password"},
        )
        workspace_id = signup.json()["workspace_id"]
        csrf = signup.json()["csrf_token"]
        root = client.get("/", follow_redirects=False)
        initial = client.get("/")
        page = client.get(f"/w/{workspace_id}/home")
        projection = client.get(f"/api/v1/workspaces/{workspace_id}/dashboard")
        saved = client.put(
            f"/api/v1/workspaces/{workspace_id}/dashboard/layout",
            headers={**origin, "X-CSRF-Token": csrf},
            json={
                "items": [
                    {
                        "widget_key": "workspace.search",
                        "display_order": 0,
                        "size": "LARGE",
                        "settings": {},
                        "enabled": True,
                    }
                ]
            },
        )
        refreshed = client.get(f"/api/v1/workspaces/{workspace_id}/dashboard")

    assert root.status_code == 303
    assert root.headers["location"] == "/workspace-selector"
    assert initial.url.path == f"/w/{workspace_id}/home"
    assert page.status_code == 200
    assert page.text.count(f'href="/w/{workspace_id}/home"') >= 2
    assert 'data-widget-key="workspace.search"' in page.text
    assert projection.status_code == 200
    assert {item["widget_key"] for item in projection.json()["widgets"]} == {
        "calendar.today",
        "favorites",
        "notes.mine",
        "notes.recent",
        "recent_views",
        "workspace.activity",
        "workspace.search",
    }
    assert saved.status_code == 200
    assert saved.json()["layout"] == [
        {
            "widget_key": "workspace.search",
            "display_order": 0,
            "size": "LARGE",
            "settings": {},
            "enabled": True,
        }
    ]
    assert [item["widget_key"] for item in refreshed.json()["widgets"]] == [
        "workspace.search"
    ]
