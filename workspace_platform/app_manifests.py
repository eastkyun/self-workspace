"""Validated declarative app manifest loaded from the global JSON source."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.app_registry import REGISTRY_PATH

from .settings_schema import validate_schema


class ManifestRoutes(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    ui: str
    api_v1: str | None = None
    public: str | None = None

    @staticmethod
    def _safe_path(value: str, prefix: str, label: str) -> str:
        segments = value.split("/")
        if (
            not value.startswith(prefix)
            or value.startswith("//")
            or any(character in value for character in ("\\", "?", "#", "%"))
            or any(segment in {"", ".", ".."} for segment in segments[1:])
            or any(character.isspace() or ord(character) < 32 for character in value)
        ):
            raise ValueError(f"{label} must be a canonical same-origin path")
        return value

    @field_validator("ui")
    @classmethod
    def valid_ui(cls, value: str) -> str:
        return cls._safe_path(value, "/w/{workspace_id}/", "ui route")

    @field_validator("api_v1")
    @classmethod
    def valid_api(cls, value: str | None) -> str | None:
        return None if value is None else cls._safe_path(
            value, "/api/v1/workspaces/{workspace_id}/", "api_v1 route"
        )

    @field_validator("public")
    @classmethod
    def valid_public(cls, value: str | None) -> str | None:
        return None if value is None else cls._safe_path(value, "/p/", "public route")


class ManifestSidebar(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    visible: bool
    label: str = Field(min_length=1, max_length=60)
    order: int = Field(ge=0)


class ManifestPermission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: str = Field(pattern=r"^[a-z][a-z0-9-]*\.[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=240)
    supports_own: bool = False
    owner_only: bool = False


class ManifestWidget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    name: str = Field(min_length=1, max_length=80)
    required_permissions: tuple[str, ...] = ()
    default_size: Literal["SMALL", "MEDIUM", "LARGE"] = "MEDIUM"
    settings_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": False})

    @field_validator("settings_schema")
    @classmethod
    def valid_settings_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_schema(value)
        return value


class ManifestCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    search: bool = False
    dashboard: bool = False
    internal_links: bool = False


class DefaultEnablement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    personal: bool
    team: bool


class PlatformManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    builtin: bool = True
    status: str = Field(pattern=r"^(ACTIVE|DEPRECATED|DISABLED)$")
    routes: ManifestRoutes
    sidebar: ManifestSidebar
    launcher: bool
    home_card: bool
    permissions: tuple[ManifestPermission, ...]
    settings_schema: dict[str, Any]
    widgets: tuple[ManifestWidget, ...] = ()
    capabilities: ManifestCapabilities = ManifestCapabilities()
    default_enabled: DefaultEnablement

    @field_validator("settings_schema")
    @classmethod
    def valid_settings_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        validate_schema(value)
        return value

    @model_validator(mode="after")
    def codes_belong_to_app(self) -> "PlatformManifest":
        codes = [permission.code for permission in self.permissions]
        if len(codes) != len(set(codes)):
            raise ValueError("permission codes must be unique")
        if any(not code.startswith(f"{self.key}.") for code in codes):
            raise ValueError("permission code must belong to its app")
        widget_keys = [widget.key for widget in self.widgets]
        if len(widget_keys) != len(set(widget_keys)):
            raise ValueError("widget keys must be unique")
        declared = set(codes)
        for widget in self.widgets:
            if len(widget.required_permissions) != len(set(widget.required_permissions)):
                raise ValueError("widget permissions must be unique")
            if not set(widget.required_permissions).issubset(declared):
                raise ValueError("widget permissions must belong to its app")
        return self

    def canonical_payload(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": metadata["id"],
            "name": metadata["name"],
            "display_name": metadata["display_name"],
            "description": metadata["description"],
            "icon": metadata["icon"],
            "category": metadata["category"],
            "version": metadata["version"],
            "platform": self.model_dump(mode="json"),
        }


def canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def manifest_checksum(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def load_builtin_manifest_records(path: str | Path = REGISTRY_PATH) -> tuple[tuple[dict[str, Any], PlatformManifest], ...]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    records: list[tuple[dict[str, Any], PlatformManifest]] = []
    seen: set[str] = set()
    for raw in payload.get("apps", []):
        if not isinstance(raw, dict) or not isinstance(raw.get("platform"), dict):
            raise ValueError("every built-in app must declare platform metadata")
        manifest = PlatformManifest.model_validate(raw["platform"])
        if manifest.key != raw.get("id") or manifest.key in seen:
            raise ValueError("platform manifest key must be unique and match app id")
        seen.add(manifest.key)
        records.append((raw, manifest))
    if not records:
        raise ValueError("at least one platform manifest is required")
    widget_keys = [widget.key for _, item in records for widget in item.widgets]
    if len(widget_keys) != len(set(widget_keys)):
        raise ValueError("widget keys must be globally unique")
    return tuple(records)


def load_builtin_manifests(path: str | Path = REGISTRY_PATH) -> tuple[PlatformManifest, ...]:
    return tuple(manifest for _, manifest in load_builtin_manifest_records(path))
