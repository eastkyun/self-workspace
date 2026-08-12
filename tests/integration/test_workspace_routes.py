import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.calendar.app.main import initialize_database
from apps.calendar.migrations import TARGET_VERSION as CALENDAR_WORKSPACE_SCHEMA_VERSION
from portal.main import create_app


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_portal_creates_new_database_and_serves_nested_calendar(tmp_path: Path) -> None:
    database_path = tmp_path / "calendar.db"
    notes_database_path = tmp_path / "notes.db"

    with TestClient(
        create_app(database_path, notes_database_path=notes_database_path)
    ) as client:
        portal = client.get("/")
        shell_stylesheet = client.get("/static/shared/shell.css")
        shell_script = client.get("/static/shared/shell.js")
        hub_stylesheet = client.get("/static/hub.css")
        hub_script = client.get("/static/hub.js")
        portal_state = client.get("/static/hub-state.js")
        portal_icon = client.get("/static/icons/calendar.svg")
        registry = client.get("/api/hub/apps")
        calendar_redirect = client.get("/apps/calendar", follow_redirects=False)
        canonical_calendar_redirect = client.get(
            "/w/11111111-1111-1111-1111-111111111111/calendar",
            follow_redirects=False,
        )
        calendar = client.get("/apps/calendar/")
        calendar_shell_stylesheet = client.get(
            "/apps/calendar/static/shared/shell.css"
        )
        calendar_shell_script = client.get("/apps/calendar/static/shared/shell.js")
        stylesheet = client.get("/apps/calendar/static/styles.css")
        calendar_markdown_stylesheet = client.get(
            "/apps/calendar/static/shared/markdown/markdown.css"
        )
        tasks = client.get("/apps/calendar/api/tasks")
        notes_redirect = client.get("/apps/notes", follow_redirects=False)
        canonical_notes_redirect = client.get(
            "/w/11111111-1111-1111-1111-111111111111/notes?note_id=7",
            follow_redirects=False,
        )
        notes = client.get("/apps/notes/")
        notes_new = client.get("/apps/notes/new")
        unsafe_login = client.get("/login", params={"next": "//evil.example"})
        notes_stylesheet = client.get("/apps/notes/static/notes.css")
        notes_markdown_stylesheet = client.get(
            "/apps/notes/static/shared/markdown/markdown.css"
        )
        notes_markdown_module = client.get(
            "/apps/notes/static/shared/markdown/index.js"
        )
        notes_script = client.get("/apps/notes/static/notes.js")
        notes_api = client.get("/apps/notes/api/notes")
        internal_links = client.post(
            "/api/internal-links/resolve",
            json={"links": [{"type": "note", "target": "1"}]},
        )

    assert portal.status_code == 200
    assert 'id="sidebar"' in portal.text
    assert 'id="launcher"' in portal.text
    assert 'class="workspace-header"' in portal.text
    assert 'class="hub-quote"' in portal.text
    assert 'data-quote-id="' in portal.text
    assert "<cite>" in portal.text
    assert portal.text.count('aria-current="page"') == 1
    assert ">Home</span>" in portal.text
    assert shell_stylesheet.status_code == 200
    assert shell_script.status_code == 200
    assert hub_stylesheet.status_code == 200
    assert hub_script.status_code == 200
    assert portal_state.status_code == 200
    assert portal_icon.status_code == 200
    assert registry.status_code == 200
    assert registry.json()[0]["route"] == "/apps/calendar/"
    assert registry.json()[0]["display_name"] == "Calendar"
    assert calendar_redirect.status_code == 307
    assert calendar_redirect.headers["location"] == "/login?next=%2Fapps%2Fcalendar"
    assert canonical_calendar_redirect.status_code == 307
    assert canonical_calendar_redirect.headers["location"] == (
        "/login?next=%2Fw%2F11111111-1111-1111-1111-111111111111%2Fcalendar"
    )
    assert calendar.status_code == 200
    assert "Log in · My Workspace" in calendar.text
    assert calendar_shell_stylesheet.status_code == 200
    assert calendar_shell_script.status_code == 200
    assert stylesheet.status_code == 200
    assert calendar_markdown_stylesheet.status_code == 200
    assert tasks.status_code == 401
    assert notes_redirect.status_code == 307
    assert notes_redirect.headers["location"] == "/login?next=%2Fapps%2Fnotes"
    assert canonical_notes_redirect.status_code == 307
    assert canonical_notes_redirect.headers["location"] == (
        "/login?next=%2Fw%2F11111111-1111-1111-1111-111111111111%2Fnotes%3Fnote_id%3D7"
    )
    assert notes.status_code == 200
    assert "Log in · My Workspace" in notes.text
    assert 'data-success-url="/apps/notes/"' in notes.text
    assert notes_new.status_code == 200
    assert 'data-success-url="/apps/notes/new"' in notes_new.text
    assert 'data-success-url="/"' in unsafe_login.text
    assert notes_stylesheet.status_code == 200
    assert notes_markdown_stylesheet.status_code == 200
    assert notes_markdown_module.status_code == 200
    assert notes_script.status_code == 200
    assert notes_api.status_code == 404
    assert internal_links.status_code == 200
    assert internal_links.json() == [
        {
            "type": "note",
            "target": "1",
            "exists": False,
            "href": None,
            "label": "note:1",
        }
    ]

    with sqlite3.connect(database_path) as connection:
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == CALENDAR_WORKSPACE_SCHEMA_VERSION
        )


def test_portal_legacy_adapters_share_workspace_services_without_app_mounts(
    tmp_path: Path,
) -> None:
    application = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
    )
    assert application.state.calendar_service.database_path == (
        tmp_path / "calendar.db"
    )
    assert (
        application.state.legacy_notes_service.source_resolver
        is application.state.source_resolver
    )
    assert any(route.path == "/apps/notes" for route in application.routes)
    assert not any(
        route.path == "/apps/notes" and hasattr(route.app, "state")
        for route in application.routes
    )
    assert not any(
        route.path == "/apps/calendar" and hasattr(route.app, "state")
        for route in application.routes
    )


def test_legacy_internal_link_endpoint_hides_workspace_scoped_resources(
    tmp_path: Path,
) -> None:
    with TestClient(
        create_app(
            tmp_path / "calendar.db",
            notes_database_path=tmp_path / "notes.db",
        )
    ) as client:
        response = client.post(
            "/api/internal-links/resolve",
            json={
                "links": [
                    {"type": "note", "target": "1"},
                    {"type": "calendar", "target": "1"},
                ]
            },
        )

    assert response.status_code == 200
    assert response.json() == [
        {
            "type": "note",
            "target": "1",
            "exists": False,
            "href": None,
            "label": "note:1",
        },
        {
            "type": "calendar",
            "target": "1",
            "exists": False,
            "href": None,
            "label": "calendar:1",
        },
    ]


def test_portal_rejects_existing_v2_database_without_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "calendar.db"
    initialize_database(database_path)
    before = sha256(database_path)

    with pytest.raises(RuntimeError, match="offline migration"):
        with TestClient(
            create_app(
                database_path,
                notes_database_path=tmp_path / "notes.db",
            )
        ):
            pass

    assert sha256(database_path) == before


def test_portal_uses_calendar_database_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "environment-calendar.db"
    notes_database_path = tmp_path / "environment-notes.db"
    publishing_database_path = tmp_path / "environment-publishing.db"
    asset_database_path = tmp_path / "environment-assets.db"
    publication_types_path = tmp_path / "environment-publication-types.json"
    monkeypatch.setenv("CALENDAR_DB_PATH", str(database_path))
    monkeypatch.setenv("NOTES_DB_PATH", str(notes_database_path))
    monkeypatch.setenv("PUBLISHING_DB_PATH", str(publishing_database_path))
    monkeypatch.setenv("ASSET_DB_PATH", str(asset_database_path))
    monkeypatch.setenv("PUBLICATION_TYPES_PATH", str(publication_types_path))

    application = create_app()

    assert application.state.calendar_database_path == database_path
    assert application.state.notes_database_path == notes_database_path
    assert application.state.publishing_database_path == publishing_database_path
    assert application.state.asset_database_path == asset_database_path
    assert application.state.publication_type_manifest_store.path == (
        publication_types_path
    )
    with TestClient(application) as client:
        assert client.get("/apps/calendar/api/tasks").status_code == 401
    assert database_path.is_file()
    assert notes_database_path.is_file()


def test_portal_rejects_legacy_database_without_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 1")
    before = sha256(database_path)

    with pytest.raises(RuntimeError, match="offline migration"):
        with TestClient(
            create_app(
                database_path,
                notes_database_path=tmp_path / "notes.db",
            )
        ):
            pass

    assert sha256(database_path) == before


def test_calendar_frontend_uses_explicit_workspace_api_and_static_bases() -> None:
    static_dir = Path("apps/calendar/app/static")
    template = Path("apps/calendar/app/templates/calendar.html").read_text(
        encoding="utf-8"
    )
    styles = (static_dir / "styles.css").read_text(encoding="utf-8")
    script = (static_dir / "app.js").read_text(encoding="utf-8")

    assert 'data-api-base="{{ calendar_api_base }}"' in template
    assert 'href="/static/shared/markdown/markdown.css"' in template
    assert 'src="/static/' not in template
    assert 'url("/static/' not in styles
    assert 'const apiBase = calendarPage.dataset.apiBase' in script
    assert 'from "/static/shared/markdown/index.js"' in script


def test_portal_runtime_assets_do_not_reference_external_hosts() -> None:
    paths = [
        Path("portal/templates/home.html"),
        Path("portal/static/hub.js"),
        Path("portal/static/hub.css"),
        Path("shared/templates/app_shell.html"),
        Path("shared/static/shell.js"),
        Path("shared/static/shell.css"),
        Path("apps/notes/app/templates/notes.html"),
        Path("apps/notes/app/static/notes.js"),
        Path("apps/notes/app/static/notes.css"),
    ]

    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "http://" not in source
        assert "https://" not in source
        assert "//cdn." not in source


def test_hub_reuses_the_shared_shell_account_request() -> None:
    shell_script = Path("shared/static/shell.js").read_text(encoding="utf-8")
    hub_script = Path("portal/static/hub.js").read_text(encoding="utf-8")

    assert "workspaceAccountPromise" in shell_script
    assert "workspaceAccountPromise" in hub_script
    assert "api/v1/users/me" not in hub_script
