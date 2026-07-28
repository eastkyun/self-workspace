import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from portal.registry import HubApp, load_apps, navigation_apps


def test_registry_loads_internal_calendar_app() -> None:
    apps = load_apps()

    assert [app.id for app in apps] == ["calendar"]
    assert apps[0].url == "/apps/calendar/"
    assert apps[0].enabled is True
    assert apps[0].navigationVisible is True
    assert apps[0].defaultPinned is True


@pytest.mark.parametrize(
    ("kind", "url"),
    [
        ("internal", "/calendar"),
        ("external", "http://example.com"),
        ("external", "javascript:alert(1)"),
    ],
)
def test_registry_rejects_unsafe_urls(kind: str, url: str) -> None:
    with pytest.raises(ValidationError):
        HubApp(
            id="unsafe",
            name="Unsafe",
            kind=kind,
            url=url,
            icon="/static/icons/calendar.svg",
            description="invalid",
        )


def test_registry_rejects_duplicate_ids(tmp_path: Path) -> None:
    calendar = {
        "id": "calendar",
        "name": "Calendar",
        "kind": "internal",
        "url": "/apps/calendar/",
        "icon": "/static/icons/calendar.svg",
        "description": "calendar",
    }
    registry_path = tmp_path / "apps.json"
    registry_path.write_text(
        json.dumps({"apps": [calendar, calendar]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="unique"):
        load_apps(registry_path)


def test_registry_filters_disabled_apps_and_hidden_navigation(tmp_path: Path) -> None:
    registry_path = tmp_path / "apps.json"
    registry_path.write_text(
        json.dumps(
            {
                "apps": [
                    {
                        "id": "disabled",
                        "name": "Disabled",
                        "kind": "internal",
                        "url": "/apps/disabled/",
                        "icon": "/static/icons/calendar.svg",
                        "description": "disabled",
                        "enabled": False,
                        "order": 1,
                    },
                    {
                        "id": "hidden",
                        "name": "Hidden",
                        "kind": "internal",
                        "url": "/apps/hidden/",
                        "icon": "/static/icons/calendar.svg",
                        "description": "hidden from navigation",
                        "navigationVisible": False,
                        "order": 2,
                    },
                    {
                        "id": "calendar",
                        "name": "Calendar",
                        "kind": "internal",
                        "url": "/apps/calendar/",
                        "icon": "/static/icons/calendar.svg",
                        "description": "calendar",
                        "order": 3,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    apps = load_apps(registry_path)

    assert [app.id for app in apps] == ["hidden", "calendar"]
    assert [app.id for app in navigation_apps(apps)] == ["calendar"]
    assert [app.id for app in load_apps(registry_path, include_disabled=True)] == [
        "disabled",
        "hidden",
        "calendar",
    ]
