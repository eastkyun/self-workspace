"""Immutable Phase 1 catalog and built-in role seed definitions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import NAMESPACE_URL, uuid5

from workspace_platform.app_manifests import load_builtin_manifest_records


@dataclass(frozen=True, slots=True)
class FoundationApp:
    key: str
    name: str
    description: str
    icon: str
    category: str
    version: str
    display_order: int
    permissions: tuple[str, ...]
    default_enabled: bool

    @property
    def id(self) -> str:
        return str(uuid5(NAMESPACE_URL, f"my-workspace:foundation-app:{self.key}"))


CRUD_PERMISSIONS = (
    "read_all",
    "create",
    "update_own",
    "update_all",
    "delete_own",
    "delete_all",
    "manage",
)

WORKSPACE_PERMISSIONS = (
    "settings.manage",
    "apps.manage",
    "members.invite",
    "members.remove",
    "roles.assign",
    "permissions.manage",
    "ownership.transfer",
    "delete",
)

OWNER_ONLY_PERMISSION_CODES = frozenset(
    {"workspace.ownership.transfer", "workspace.delete"}
)

def _foundation_apps() -> tuple[FoundationApp, ...]:
    apps: list[FoundationApp] = []
    for raw, manifest in load_builtin_manifest_records():
        apps.append(
            FoundationApp(
                key=manifest.key,
                name=raw["display_name"],
                description=raw["description"],
                icon=raw["icon"],
                category=raw["category"],
                version=raw["version"],
                display_order=manifest.sidebar.order,
                permissions=tuple(
                    permission.code.removeprefix(f"{manifest.key}.")
                    for permission in manifest.permissions
                ),
                default_enabled=manifest.default_enabled.personal,
            )
        )
    return tuple(apps)


FOUNDATION_APPS = _foundation_apps()

BUILTIN_ROLES = (
    ("owner", "Owner", "OWNER"),
    ("admin", "Admin", "ADMIN"),
    ("member", "Member", "MEMBER"),
    ("viewer", "Viewer", "VIEWER"),
)


def permission_code(app: FoundationApp, permission: str) -> str:
    return f"{app.key}.{permission}"


def permission_id(code: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"my-workspace:permission:{code}"))


def role_permission_codes(role_key: str) -> frozenset[str]:
    all_codes = {
        permission_code(app, permission)
        for app in FOUNDATION_APPS
        for permission in app.permissions
    }
    if role_key in {"owner", "admin"}:
        return frozenset(
            code
            for code in all_codes
            if role_key == "owner" or code not in OWNER_ONLY_PERMISSION_CODES
        )
    if role_key == "member":
        allowed = {"read_all", "create", "update_own", "delete_own"}
        return frozenset(
            permission_code(app, permission)
            for app in FOUNDATION_APPS
            for permission in app.permissions
            if app.key != "workspace" and permission in allowed
        )
    if role_key == "viewer":
        return frozenset(
            permission_code(app, permission)
            for app in FOUNDATION_APPS
            for permission in app.permissions
            if app.key != "workspace" and permission == "read_all"
        )
    raise ValueError(f"unknown built-in role: {role_key}")


def foundation_catalog_checksum() -> str:
    payload = [_app_payload(app) for app in FOUNDATION_APPS]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def foundation_app_checksum(app: FoundationApp) -> str:
    encoded = json.dumps(
        _app_payload(app), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _app_payload(app: FoundationApp) -> dict[str, object]:
    return {
        "key": app.key,
        "name": app.name,
        "description": app.description,
        "icon": app.icon,
        "category": app.category,
        "version": app.version,
        "permissions": list(app.permissions),
        "default_enabled": app.default_enabled,
    }
