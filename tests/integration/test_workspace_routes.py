import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.calendar.app.main import SCHEMA_VERSION, initialize_database
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
        notes = client.get("/apps/notes/")
        notes_new = client.get("/apps/notes/new")
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
    assert calendar_redirect.status_code in {307, 308}
    assert calendar_redirect.headers["location"].endswith("/apps/calendar/")
    assert calendar.status_code == 200
    assert 'id="sidebar"' in calendar.text
    assert 'class="workspace-header"' in calendar.text
    assert calendar.text.count('aria-current="page"') == 1
    assert 'data-app-id="calendar"' in calendar.text
    assert 'id="calendar"' in calendar.text
    assert calendar_shell_stylesheet.status_code == 200
    assert calendar_shell_script.status_code == 200
    assert stylesheet.status_code == 200
    assert calendar_markdown_stylesheet.status_code == 200
    assert tasks.status_code == 200
    assert tasks.json() == []
    assert notes_redirect.status_code in {307, 308}
    assert notes_redirect.headers["location"].endswith("/apps/notes/")
    assert notes.status_code == 200
    assert notes_new.status_code == 200
    assert 'data-current-app="notes"' in notes.text
    assert notes.text.count('aria-current="page"') == 1
    assert notes_stylesheet.status_code == 200
    assert notes_markdown_stylesheet.status_code == 200
    assert notes_markdown_module.status_code == 200
    assert notes_script.status_code == 200
    assert notes_api.status_code == 200
    assert notes_api.json() == []
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_portal_calendar_and_notes_share_one_registry_snapshot(
    tmp_path: Path,
) -> None:
    application = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
    )
    calendar_mount = next(
        route for route in application.routes if route.path == "/apps/calendar"
    )
    notes_mount = next(
        route for route in application.routes if route.path == "/apps/notes"
    )

    assert calendar_mount.app.state.app_registry is application.state.app_registry
    assert notes_mount.app.state.app_registry is application.state.app_registry
    assert (
        notes_mount.app.state.notes_service.source_resolver
        is application.state.source_resolver
    )


def test_internal_link_endpoint_resolves_notes_and_calendar_events(
    tmp_path: Path,
) -> None:
    with TestClient(
        create_app(
            tmp_path / "calendar.db",
            notes_database_path=tmp_path / "notes.db",
        )
    ) as client:
        event = client.post(
            "/apps/calendar/api/tasks",
            json={
                "title": "연결 일정",
                "start_at": "2026-07-28T09:00:00+09:00",
                "due_at": "2026-07-28T10:00:00+09:00",
            },
        ).json()
        note = client.post(
            "/apps/notes/api/notes",
            json={
                "title": "연결 노트",
                "content": "",
                "note_type": "general",
                "is_pinned": False,
            },
        ).json()
        response = client.post(
            "/api/internal-links/resolve",
            json={
                "links": [
                    {"type": "note", "target": str(note["id"])},
                    {"type": "calendar", "target": str(event["id"])},
                ]
            },
        )

    assert response.status_code == 200
    assert response.json() == [
        {
            "type": "note",
            "target": str(note["id"]),
            "exists": True,
            "href": f"/apps/notes/?note_id={note['id']}",
            "label": "연결 노트",
        },
        {
            "type": "calendar",
            "target": str(event["id"]),
            "exists": True,
            "href": f"/apps/calendar/?event_id={event['id']}",
            "label": "연결 일정",
        },
    ]


def test_portal_verifies_existing_v2_database_without_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "calendar.db"
    initialize_database(database_path)
    before = sha256(database_path)

    with TestClient(
        create_app(
            database_path,
            notes_database_path=tmp_path / "notes.db",
        )
    ) as client:
        assert client.get("/api/hub/apps").status_code == 200

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
        assert client.get("/apps/calendar/api/tasks").json() == []
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


def test_calendar_frontend_uses_mount_relative_urls() -> None:
    static_dir = Path("apps/calendar/app/static")
    template = Path("apps/calendar/app/templates/calendar.html").read_text(
        encoding="utf-8"
    )
    styles = (static_dir / "styles.css").read_text(encoding="utf-8")
    script = (static_dir / "app.js").read_text(encoding="utf-8")

    assert 'href="/static/' not in template
    assert 'src="/static/' not in template
    assert 'url("/static/' not in styles
    assert 'new URL("./", document.baseURI)' in script
    assert 'request("/api/' not in script


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
