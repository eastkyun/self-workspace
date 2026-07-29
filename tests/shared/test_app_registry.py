from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from shared.app_registry import (
    JsonRegistrySource,
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


def test_rejects_unsupported_schema_version(tmp_path: Path) -> None:
    data = payload(app())
    data["schema_version"] = 2

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


def test_registry_json_serialization_is_deterministic(tmp_path: Path) -> None:
    data = payload(app())
    registry_path = tmp_path / "apps.json"
    first = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    second = json.dumps(deepcopy(data), ensure_ascii=False, indent=2) + "\n"

    registry_path.write_text(first, encoding="utf-8")

    assert registry_path.read_text(encoding="utf-8") == second
