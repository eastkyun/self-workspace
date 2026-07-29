from pathlib import Path

from fastapi.testclient import TestClient

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


def test_default_registry_loads_calendar_and_notes_apps() -> None:
    app_registry = AppRegistry.load_default()

    assert [app.id for app in app_registry.get_all()] == ["calendar", "notes"]
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
