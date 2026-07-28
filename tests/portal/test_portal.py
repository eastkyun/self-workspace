import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from portal.registry import HubApp, load_apps


def test_registry_loads_internal_calendar_app() -> None:
    apps = load_apps()

    assert [app.id for app in apps] == ["calendar"]
    assert apps[0].url == "/apps/calendar/"
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
