from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from shared.app_registry import (
    AppRegistry,
    JsonRegistrySource,
    RegistryDocument,
    RegistryValidationError,
    normalize_internal_route,
    parse_registry_payload,
)


def category(
    category_id: str = "productivity",
    *,
    order: int = 10,
) -> dict:
    return {
        "id": category_id,
        "display_name": category_id.title(),
        "order": order,
        "description": "",
    }


def app(
    app_id: str = "calendar",
    *,
    route: str = "/apps/calendar/",
    category_id: str = "productivity",
    order: int = 10,
) -> dict:
    return {
        "id": app_id,
        "name": app_id,
        "display_name": app_id.title(),
        "description": "Test app",
        "icon": "/static/icons/calendar.svg",
        "route": route,
        "category": category_id,
        "enabled": True,
        "default_pinned": False,
        "navigation_visible": True,
        "launcher_visible": True,
        "order": order,
        "version": "1.0.0",
        "kind": "internal",
        "search_keywords": [],
    }


def payload(*apps: dict, categories: list[dict] | None = None) -> dict:
    return {
        "schema_version": 1,
        "categories": categories or [category()],
        "apps": list(apps),
    }


def parse(data: dict, tmp_path: Path):
    icon = tmp_path / "icons" / "calendar.svg"
    icon.parent.mkdir(parents=True, exist_ok=True)
    icon.write_text("<svg/>", encoding="utf-8")
    return parse_registry_payload(data, source="test", asset_root=tmp_path)


def issue_codes(error: RegistryValidationError) -> set[str]:
    return {issue.code for issue in error.issues}


def test_loads_canonical_registry_document(tmp_path: Path) -> None:
    document = parse(payload(app()), tmp_path)

    assert document.schema_version == 1
    assert isinstance(document.apps, tuple)
    assert isinstance(document.categories, tuple)
    assert document.apps[0].route == "/apps/calendar/"


def test_internal_routes_have_one_canonical_trailing_slash() -> None:
    assert normalize_internal_route("/apps/calendar") == "/apps/calendar/"
    assert normalize_internal_route("/apps/calendar/") == "/apps/calendar/"


@pytest.mark.parametrize("schema_version", [2, True, 1.0, "1"])
def test_rejects_unsupported_schema_version(
    tmp_path: Path,
    schema_version,
) -> None:
    data = payload(app())
    data["schema_version"] = schema_version

    with pytest.raises(RegistryValidationError) as caught:
        parse(data, tmp_path)

    assert issue_codes(caught.value) == {"unsupported_schema_version"}


def test_collects_independent_validation_issues(tmp_path: Path) -> None:
    broken = app()
    del broken["display_name"]
    broken["version"] = "latest"
    data = payload(
        broken,
        app(
            "calendar",
            route="/apps/calendar",
            category_id="missing",
            order=10,
        ),
    )
    data["unexpected"] = True

    with pytest.raises(RegistryValidationError) as caught:
        parse(data, tmp_path)

    codes = issue_codes(caught.value)
    assert "missing" in codes
    assert "string_pattern_mismatch" in codes
    assert "extra_forbidden" in codes
    assert "unknown_category" in codes


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda data: data["apps"].append(
                app("calendar-copy", route="/apps/calendar", order=20)
            ),
            "duplicate_route",
        ),
        (
            lambda data: data["categories"].append(category("productivity", order=20)),
            "duplicate_category_id",
        ),
        (
            lambda data: data["categories"].append(category("knowledge", order=10)),
            "duplicate_category_order",
        ),
        (
            lambda data: data["apps"].append(
                app("notes", route="/apps/notes/", order=10)
            ),
            "duplicate_app_order",
        ),
    ],
)
def test_rejects_registry_collisions(tmp_path: Path, mutate, code: str) -> None:
    data = payload(app())
    mutate(data)

    with pytest.raises(RegistryValidationError) as caught:
        parse(data, tmp_path)

    assert code in issue_codes(caught.value)


def test_allows_same_app_order_in_different_categories(tmp_path: Path) -> None:
    data = payload(
        app(),
        app("notes", route="/apps/notes/", category_id="knowledge", order=10),
        categories=[category(), category("knowledge", order=20)],
    )

    document = parse(data, tmp_path)

    assert len(document.apps) == 2


def test_rejects_missing_icon_and_disabled_default_pin(tmp_path: Path) -> None:
    data = payload(app())
    data["apps"][0]["icon"] = "/static/icons/missing.svg"
    data["apps"][0]["enabled"] = False
    data["apps"][0]["default_pinned"] = True

    with pytest.raises(RegistryValidationError) as caught:
        parse_registry_payload(data, source="test", asset_root=tmp_path)

    assert issue_codes(caught.value) == {
        "disabled_default_pinned",
        "missing_icon",
    }


@pytest.mark.parametrize(
    "route",
    [
        "http://example.com",
        "javascript:alert(1)",
        "http://localhost:8000",
    ],
)
def test_external_apps_require_https(
    tmp_path: Path,
    route: str,
) -> None:
    external = app("external", route=route)
    external["kind"] = "external"

    with pytest.raises(RegistryValidationError):
        parse(payload(external), tmp_path)


def test_json_source_reports_invalid_json(tmp_path: Path) -> None:
    registry_path = tmp_path / "apps.json"
    registry_path.write_text("{", encoding="utf-8")

    with pytest.raises(RegistryValidationError) as caught:
        JsonRegistrySource(registry_path, asset_root=tmp_path).load()

    assert issue_codes(caught.value) == {"source_error"}


def registry(data: dict, tmp_path: Path) -> AppRegistry:
    document = parse(data, tmp_path)
    return AppRegistry(document)


def test_query_results_are_immutable_and_deterministically_sorted(
    tmp_path: Path,
) -> None:
    data = payload(
        app("zeta", route="/apps/zeta/", order=20),
        app("alpha", route="/apps/alpha/", order=10),
    )
    result = registry(data, tmp_path)

    assert isinstance(result.get_all(), tuple)
    assert isinstance(result.get_enabled(), tuple)
    assert isinstance(result.get_navigation(), tuple)
    assert isinstance(result.get_launcher(), tuple)
    assert [item.id for item in result.get_all()] == ["alpha", "zeta"]


def test_filters_disabled_apps_from_all_portal_surfaces_and_defaults(
    tmp_path: Path,
) -> None:
    disabled = app("disabled", route="/apps/disabled/", order=20)
    disabled["enabled"] = False
    disabled["navigation_visible"] = True
    disabled["launcher_visible"] = True
    visible = app()
    visible["default_pinned"] = True
    result = registry(payload(visible, disabled), tmp_path)

    assert [item.id for item in result.get_all()] == ["calendar", "disabled"]
    assert [item.id for item in result.get_enabled()] == ["calendar"]
    assert [item.id for item in result.get_navigation()] == ["calendar"]
    assert [item.id for item in result.get_launcher()] == ["calendar"]
    assert result.get_default_pinned_ids() == ("calendar",)


def test_lookup_exists_and_categories(tmp_path: Path) -> None:
    result = registry(payload(app()), tmp_path)

    assert result.get_app("calendar").display_name == "Calendar"
    assert result.get_app("missing") is None
    assert result.exists("calendar") is True
    assert result.exists("missing") is False
    assert [item.id for item in result.categories()] == ["productivity"]


def test_category_then_app_order_is_deterministic(tmp_path: Path) -> None:
    data = payload(
        app("notes", route="/apps/notes/", category_id="knowledge", order=5),
        app("calendar", order=20),
        categories=[category("knowledge", order=20), category(order=10)],
    )

    assert [item.id for item in registry(data, tmp_path).get_all()] == [
        "calendar",
        "notes",
    ]


def test_registry_json_serialization_is_canonical(tmp_path: Path) -> None:
    categories = [category("knowledge", order=20), category(order=10)]
    apps = [
        app("notes", route="/apps/notes/", category_id="knowledge", order=5),
        app("calendar", order=20),
    ]
    first = registry(payload(*apps, categories=categories), tmp_path)
    second = registry(
        payload(*reversed(apps), categories=list(reversed(categories))),
        tmp_path,
    )

    assert first.to_json() == second.to_json()
    assert first.to_json().index('"id": "productivity"') < first.to_json().index(
        '"id": "knowledge"'
    )
    assert first.to_json().index('"id": "calendar"') < first.to_json().index(
        '"id": "notes"'
    )


def test_search_is_case_insensitive_and_normalizes_whitespace(
    tmp_path: Path,
) -> None:
    calendar = app()
    calendar["description"] = "Monthly   Planning"
    calendar["search_keywords"] = ["Team Schedule"]
    disabled = app("notes", route="/apps/notes/", order=20)
    disabled["enabled"] = False
    result = registry(payload(calendar, disabled), tmp_path)

    assert [item.id for item in result.search("  MONTHLY planning ")] == ["calendar"]
    assert [item.id for item in result.search("team schedule")] == ["calendar"]
    assert [item.id for item in result.search("   ")] == ["calendar"]
    assert [item.id for item in result.search("", include_disabled=True)] == [
        "calendar",
        "notes",
    ]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/apps/calendar", "calendar"),
        ("/apps/calendar/", "calendar"),
        ("/apps/calendar/api/tasks", "calendar"),
        ("/apps/cal", None),
        ("/apps/calendarish", None),
        ("/apps", None),
    ],
)
def test_resolve_route_respects_path_segment_boundaries(
    tmp_path: Path,
    path: str,
    expected: str | None,
) -> None:
    result = registry(payload(app()), tmp_path)
    matched = result.resolve_route(path)

    assert (matched.id if matched else None) == expected


def test_resolve_route_excludes_disabled_apps(tmp_path: Path) -> None:
    disabled = app()
    disabled["enabled"] = False

    assert registry(payload(disabled), tmp_path).resolve_route(
        "/apps/calendar/"
    ) is None


def test_source_merge_reports_cross_source_collisions(tmp_path: Path) -> None:
    first_document = parse(payload(app()), tmp_path)
    second_document = parse(
        payload(
            app(
                "calendar",
                route="/apps/calendar-copy/",
                category_id="knowledge",
            ),
            categories=[category("knowledge", order=20)],
        ),
        tmp_path,
    )

    class StaticSource:
        def __init__(self, name, document):
            self.source_name = name
            self.document = document

        def load(self):
            return self.document

    with pytest.raises(RegistryValidationError) as caught:
        AppRegistry.from_sources(
            [
                StaticSource("first", first_document),
                StaticSource("second", second_document),
            ]
        )

    assert "duplicate_app_id" in issue_codes(caught.value)


def test_source_merge_aggregates_errors_from_every_source(tmp_path: Path) -> None:
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    first_path.write_text("{", encoding="utf-8")
    second_path.write_text("[]", encoding="utf-8")

    with pytest.raises(RegistryValidationError) as caught:
        AppRegistry.from_sources(
            [
                JsonRegistrySource(first_path, asset_root=tmp_path),
                JsonRegistrySource(second_path, asset_root=tmp_path),
            ]
        )

    assert {issue.source for issue in caught.value.issues} == {
        str(first_path),
        str(second_path),
    }
    assert issue_codes(caught.value) == {"document_type", "source_error"}


def test_registry_document_rejects_unsupported_version_directly() -> None:
    with pytest.raises(ValidationError, match="unsupported registry schema"):
        RegistryDocument(schema_version=2, categories=(), apps=())


def test_registry_source_cannot_bypass_schema_version_validation() -> None:
    unsupported = RegistryDocument.model_construct(
        schema_version=2,
        categories=(),
        apps=(),
    )

    class UnsupportedSource:
        source_name = "unsupported"

        def load(self):
            return unsupported

    with pytest.raises(RegistryValidationError) as caught:
        AppRegistry.from_source(UnsupportedSource())

    assert issue_codes(caught.value) == {"unsupported_schema_version"}

    with pytest.raises(RegistryValidationError, match="received 2"):
        AppRegistry(unsupported)
