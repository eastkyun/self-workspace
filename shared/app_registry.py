from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
PORTAL_DIR = WORKSPACE_ROOT / "portal"
PORTAL_STATIC_DIR = PORTAL_DIR / "static"
REGISTRY_PATH = PORTAL_DIR / "apps.json"


class HubApp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str = Field(min_length=1, max_length=60)
    kind: Literal["internal", "external"]
    url: str
    icon: str
    description: str = Field(max_length=160)
    enabled: bool = True
    navigationVisible: bool = True
    defaultPinned: bool = False
    order: int = 100

    @model_validator(mode="after")
    def validate_urls(self) -> "HubApp":
        if self.kind == "internal" and not self.url.startswith("/apps/"):
            raise ValueError("internal app URL must start with /apps/")
        if self.kind == "external" and not self.url.startswith("https://"):
            raise ValueError("external app URL must use https://")
        if not self.icon.startswith("/static/icons/"):
            raise ValueError("app icons must be local /static/icons/ assets")
        return self


def load_apps(
    registry_path: Path = REGISTRY_PATH,
    *,
    include_disabled: bool = False,
) -> list[HubApp]:
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    apps = [HubApp.model_validate(item) for item in payload["apps"]]

    app_ids = [app.id for app in apps]
    if len(app_ids) != len(set(app_ids)):
        raise ValueError("app registry IDs must be unique")

    for app in apps:
        icon_path = PORTAL_STATIC_DIR / app.icon.removeprefix("/static/")
        if not icon_path.is_file():
            raise ValueError(f"local app icon does not exist: {app.icon}")

    visible_apps = apps if include_disabled else [app for app in apps if app.enabled]
    return sorted(visible_apps, key=lambda app: (app.order, app.name.casefold()))


def navigation_apps(apps: list[HubApp]) -> list[HubApp]:
    return [app for app in apps if app.enabled and app.navigationVisible]
