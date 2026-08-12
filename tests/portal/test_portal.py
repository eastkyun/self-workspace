from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
from starlette.middleware.gzip import GZipMiddleware

from portal.main import create_app
from portal.quotes import Quote
from shared.app_registry import (
    AppCategory,
    AppManifest,
    AppRegistry,
    RegistryDocument,
)


def manifest(
    app_id: str,
    *,
    order: int,
    enabled: bool = True,
    navigation_visible: bool = True,
    launcher_visible: bool = True,
) -> AppManifest:
    return AppManifest(
        id=app_id,
        name=app_id,
        display_name=app_id.title(),
        description=f"{app_id} app",
        icon="/static/icons/calendar.svg",
        route=f"/apps/{app_id}/",
        category="productivity",
        enabled=enabled,
        default_pinned=app_id == "calendar",
        navigation_visible=navigation_visible,
        launcher_visible=launcher_visible,
        order=order,
        version="1.0.0",
    )


def registry(*apps: AppManifest) -> AppRegistry:
    return AppRegistry(
        RegistryDocument(
            schema_version=1,
            categories=(
                AppCategory(
                    id="productivity",
                    display_name="Productivity",
                    order=10,
                ),
            ),
            apps=apps,
        )
    )


def test_portal_compresses_large_responses(tmp_path: Path) -> None:
    application = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
    )

    middleware = next(
        item for item in application.user_middleware if item.cls is GZipMiddleware
    )
    assert middleware.kwargs["minimum_size"] == 1_000



def test_default_registry_loads_calendar_and_notes_apps() -> None:
    app_registry = AppRegistry.load_default()

    assert [app.id for app in app_registry.get_all()] == [
        "calendar", "notes", "publishing", "assets", "home", "search", "workspace"
    ]
    assert app_registry.get_app("calendar").route == "/apps/calendar/"
    assert app_registry.get_app("calendar").navigation_visible is True
    assert app_registry.get_app("calendar").launcher_visible is True
    assert app_registry.get_app("notes").route == "/apps/notes/"
    assert app_registry.get_app("notes").category == "knowledge"
    assert app_registry.get_default_pinned_ids() == ("calendar", "notes")
    assert app_registry.resolve_route("/apps/notes/new").id == "notes"
    assert [app.id for app in app_registry.search("회의록")] == ["notes"]


def test_portal_surfaces_use_the_same_registry_snapshot(tmp_path: Path) -> None:
    app_registry = registry(
        manifest("calendar", order=10, launcher_visible=False),
        manifest("launcher", order=20, navigation_visible=False),
        manifest("disabled", order=30, enabled=False),
    )
    application = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
        app_registry=app_registry,
    )

    with TestClient(application) as client:
        home = client.get("/")
        api = client.get("/api/hub/apps")

    assert application.state.app_registry is app_registry
    assert api.status_code == 200
    assert [app["id"] for app in api.json()] == ["calendar", "launcher"]
    assert home.text.count('data-app-id="calendar"') == 1
    assert home.text.count('data-app-id="launcher"') == 1
    assert 'data-app-id="disabled"' not in home.text
    assert "지식 발행물" not in home.text
    assert "hub-publication" not in home.text


def test_hub_api_uses_canonical_registry_fields(tmp_path: Path) -> None:
    with TestClient(
        create_app(
            tmp_path / "calendar.db",
            notes_database_path=tmp_path / "notes.db",
        )
    ) as client:
        payload = client.get("/api/hub/apps").json()[0]

    assert payload["id"] == "calendar"
    assert payload["display_name"] == "Calendar"
    assert payload["route"] == "/apps/calendar/"
    assert payload["default_pinned"] is True
    assert "url" not in payload
    assert "defaultPinned" not in payload


def test_hub_renders_injected_quote_with_attribution(tmp_path: Path) -> None:
    selected = Quote(
        id="second",
        text="작은 진전도 진전이다.",
        author="Workspace",
        source="작업 원칙",
    )
    application = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
        quotes=(
            Quote("first", "첫 문장", "첫 저자"),
            selected,
        ),
        quote_selector=lambda quotes: quotes[-1],
    )

    with TestClient(application) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'data-quote-id="second"' in response.text
    assert "작은 진전도 진전이다." in response.text
    assert "<cite>Workspace</cite>" in response.text
    assert "작업 원칙" in response.text


def test_hub_escapes_quote_content_and_falls_back_when_empty(
    tmp_path: Path,
) -> None:
    escaped_application = create_app(
        tmp_path / "calendar-escaped.db",
        notes_database_path=tmp_path / "notes-escaped.db",
        quotes=(Quote("safe", "<script>alert(1)</script>", "<Author>"),),
    )
    fallback_application = create_app(
        tmp_path / "calendar-empty.db",
        notes_database_path=tmp_path / "notes-empty.db",
        quotes=(),
    )

    with TestClient(escaped_application) as client:
        escaped = client.get("/")
    with TestClient(fallback_application) as client:
        fallback = client.get("/")

    assert "<script>alert(1)</script>" not in escaped.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in escaped.text
    assert "&lt;Author&gt;" in escaped.text
    assert 'class="hub-quote"' not in fallback.text
    assert "좋은 오후예요" in fallback.text


def test_workspace_internal_links_require_membership_and_scope_notes(tmp_path: Path) -> None:
    calendar_path = tmp_path / "calendar.db"
    notes_path = tmp_path / "notes.db"
    application = create_app(calendar_path, notes_database_path=notes_path)
    origin = {"Origin": "http://testserver"}

    with TestClient(application) as client:
        assert client.post(
            "/api/v1/workspaces/missing/internal-links/resolve",
            json={"links": [{"type": "note", "target": "1"}]},
        ).status_code == 401
        signup = client.post(
            "/api/v1/auth/signup",
            headers=origin,
            json={"email": "owner@example.com", "password": "password"},
        )
        workspace_id = signup.json()["workspace_id"]
        user_id = signup.json()["user_id"]
        with sqlite3.connect(notes_path) as connection:
            connection.execute(
                """INSERT INTO notes (
                    title, content, note_type, is_pinned, created_at, updated_at,
                    workspace_id, created_by, updated_by, deleted_at
                ) VALUES ('Scoped note', '', 'general', 0, 'c', 'u', ?, ?, ?, NULL)""",
                (workspace_id, user_id, user_id),
            )
        response = client.post(
            f"/api/v1/workspaces/{workspace_id}/internal-links/resolve",
            json={
                "links": [
                    {"type": "note", "target": "1"},
                    {"type": "calendar", "target": "1"},
                ]
            },
        )
        assert response.status_code == 200
        assert response.json()[0] == {
            "type": "note",
            "target": "1",
            "exists": True,
            "href": f"/w/{workspace_id}/notes?note_id=1",
            "label": "Scoped note",
        }
        # Calendar v2 has no tenant key, so the canonical endpoint fails closed
        # until the coordinated v3 cutover.
        assert response.json()[1]["exists"] is False

        client.post(
            "/api/v1/auth/signup",
            headers=origin,
            json={"email": "other@example.com", "password": "password"},
        )
        assert client.post(
            f"/api/v1/workspaces/{workspace_id}/internal-links/resolve",
            json={"links": [{"type": "note", "target": "1"}]},
        ).status_code == 404


def test_notes_use_the_authenticated_workspace_id(tmp_path: Path) -> None:
    notes_path = tmp_path / "notes.db"
    application = create_app(
        tmp_path / "calendar.db", notes_database_path=notes_path
    )
    origin = {"Origin": "http://testserver"}

    with TestClient(application) as client:
        signup = client.post(
            "/api/v1/auth/signup",
            headers=origin,
            json={"email": "notes@example.com", "password": "password"},
        )
        workspace_id = signup.json()["workspace_id"]
        csrf = signup.json()["csrf_token"]
        legacy = client.get("/apps/notes/", follow_redirects=False)
        assert legacy.status_code == 307
        assert legacy.headers["location"] == f"/w/{workspace_id}/notes"
        page = client.get(f"/w/{workspace_id}/notes")
        assert page.status_code == 200
        assert f'/api/v1/workspaces/{workspace_id}/notes' in page.text
        assert f'/api/v1/workspaces/{workspace_id}/internal-links/resolve' in page.text
        assert f'href="/w/{workspace_id}/calendar"' in page.text
        assert f'href="/w/{workspace_id}/home"' in page.text

        created = client.post(
            f"/api/v1/workspaces/{workspace_id}/notes",
            headers={**origin, "X-CSRF-Token": csrf},
            json={"title": "Workspace note"},
        )
        assert created.status_code == 201
        with sqlite3.connect(notes_path) as connection:
            stored = connection.execute(
                "SELECT workspace_id, created_by FROM notes WHERE id=?",
                (created.json()["id"],),
            ).fetchone()
        assert stored == (workspace_id, signup.json()["user_id"])
        invalid = client.get(
            "/w/00000000-0000-4000-8000-000000000002/notes"
        )
        assert invalid.status_code == 404


def test_calendar_isolated_between_personal_workspaces(tmp_path: Path) -> None:
    calendar_path = tmp_path / "calendar.db"
    application = create_app(
        calendar_path,
        notes_database_path=tmp_path / "notes.db",
    )
    origin = {"Origin": "http://testserver"}

    with TestClient(application) as first, TestClient(application) as second:
        first_signup = first.post(
            "/api/v1/auth/signup",
            headers=origin,
            json={"email": "first@example.com", "password": "password"},
        ).json()
        second_signup = second.post(
            "/api/v1/auth/signup",
            headers=origin,
            json={"email": "second@example.com", "password": "password"},
        ).json()
        first_workspace = first_signup["workspace_id"]
        second_workspace = second_signup["workspace_id"]
        task_payload = {
            "title": "First workspace only",
            "start_at": "2026-08-07T09:00:00+09:00",
            "due_at": "2026-08-07T10:00:00+09:00",
        }
        created = first.post(
            f"/api/v1/workspaces/{first_workspace}/calendar/tasks",
            headers={**origin, "X-CSRF-Token": first_signup["csrf_token"]},
            json=task_payload,
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]

        assert first.get(
            f"/api/v1/workspaces/{first_workspace}/calendar/tasks"
        ).json()[0]["id"] == task_id
        assert second.get(
            f"/api/v1/workspaces/{second_workspace}/calendar/tasks"
        ).json() == []
        assert second.get(
            f"/api/v1/workspaces/{first_workspace}/calendar/tasks"
        ).status_code == 404
        assert second.patch(
            f"/api/v1/workspaces/{second_workspace}/calendar/tasks/{task_id}",
            headers={**origin, "X-CSRF-Token": second_signup["csrf_token"]},
            json={"title": "Cross-workspace overwrite"},
        ).status_code == 404
        legacy = first.get("/apps/calendar", follow_redirects=False)
        assert legacy.status_code == 307
        assert legacy.headers["location"] == f"/w/{first_workspace}/calendar"

    with sqlite3.connect(calendar_path) as connection:
        stored = connection.execute(
            "SELECT workspace_id, created_by FROM tasks WHERE id=?",
            (task_id,),
        ).fetchone()
    assert stored == (first_workspace, first_signup["user_id"])
