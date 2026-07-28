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

    with TestClient(create_app(database_path)) as client:
        portal = client.get("/")
        portal_stylesheet = client.get("/static/styles.css")
        portal_script = client.get("/static/app.js")
        portal_state = client.get("/static/hub-state.js")
        portal_icon = client.get("/static/icons/calendar.svg")
        registry = client.get("/api/hub/apps")
        calendar_redirect = client.get("/apps/calendar", follow_redirects=False)
        calendar = client.get("/apps/calendar/")
        stylesheet = client.get("/apps/calendar/static/styles.css")
        tasks = client.get("/apps/calendar/api/tasks")

    assert portal.status_code == 200
    assert 'id="sidebar"' in portal.text
    assert 'id="launcher"' in portal.text
    assert portal_stylesheet.status_code == 200
    assert portal_script.status_code == 200
    assert portal_state.status_code == 200
    assert portal_icon.status_code == 200
    assert registry.status_code == 200
    assert registry.json()[0]["url"] == "/apps/calendar/"
    assert calendar_redirect.status_code in {307, 308}
    assert calendar_redirect.headers["location"].endswith("/apps/calendar/")
    assert calendar.status_code == 200
    assert 'id="calendar"' in calendar.text
    assert stylesheet.status_code == 200
    assert tasks.status_code == 200
    assert tasks.json() == []

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_portal_verifies_existing_v2_database_without_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "calendar.db"
    initialize_database(database_path)
    before = sha256(database_path)

    with TestClient(create_app(database_path)) as client:
        assert client.get("/api/hub/apps").status_code == 200

    assert sha256(database_path) == before


def test_portal_rejects_legacy_database_without_mutation(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY)")
        connection.execute("PRAGMA user_version = 1")
    before = sha256(database_path)

    with pytest.raises(RuntimeError, match="offline migration"):
        with TestClient(create_app(database_path)):
            pass

    assert sha256(database_path) == before


def test_calendar_frontend_uses_mount_relative_urls() -> None:
    static_dir = Path("apps/calendar/app/static")
    index = (static_dir / "index.html").read_text(encoding="utf-8")
    styles = (static_dir / "styles.css").read_text(encoding="utf-8")
    script = (static_dir / "app.js").read_text(encoding="utf-8")

    assert 'href="/static/' not in index
    assert 'src="/static/' not in index
    assert 'url("/static/' not in styles
    assert 'new URL("./", document.baseURI)' in script
    assert 'request("/api/' not in script


def test_portal_runtime_assets_do_not_reference_external_hosts() -> None:
    paths = [
        Path("portal/static/index.html"),
        Path("portal/static/app.js"),
        Path("portal/static/styles.css"),
    ]

    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "http://" not in source
        assert "https://" not in source
        assert "//cdn." not in source
