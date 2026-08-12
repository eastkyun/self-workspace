"""Idempotent Identity app/permission catalog synchronization."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from identity.database import database_connection
from identity.foundation import BUILTIN_ROLES, permission_id

from .app_manifests import (
    canonical_json,
    load_builtin_manifest_records,
    manifest_checksum,
)
from .settings_schema import SettingsSchemaError, validate_settings


class CatalogDriftError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AppCatalogSnapshot:
    app_checksums: tuple[tuple[str, str], ...]
    permission_codes: tuple[str, ...]
    widget_checksums: tuple[tuple[str, str], ...]


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class AppCatalogService:
    def __init__(self, database_path: str | Path, *, manifest_path: str | Path | None = None) -> None:
        self.database_path = Path(database_path).resolve()
        self.manifest_path = manifest_path

    def sync(self, *, runtime_app_keys: frozenset[str]) -> AppCatalogSnapshot:
        records = load_builtin_manifest_records(self.manifest_path) if self.manifest_path else load_builtin_manifest_records()
        desired_keys = {manifest.key for _, manifest in records}
        missing_runtime = sorted(
            manifest.key
            for _, manifest in records
            if manifest.status == "ACTIVE" and manifest.key not in runtime_app_keys
        )
        if missing_runtime:
            raise CatalogDriftError(f"active manifests lack explicit runtime wiring: {missing_runtime}")
        stamp = _stamp()
        with database_connection(self.database_path) as tx:
            expected_roles = {(key, name, system_role) for key, name, system_role in BUILTIN_ROLES}
            actual_roles = tx.query(
                "SELECT workspace_id, key, name, system_role, status FROM roles WHERE builtin=1"
            )
            by_workspace: dict[str, set[tuple[str, str, str]]] = {}
            for role in actual_roles:
                if role["status"] != "ACTIVE":
                    raise CatalogDriftError("built-in role definition drift detected")
                by_workspace.setdefault(role["workspace_id"], set()).add(
                    (role["key"], role["name"], role["system_role"])
                )
            workspace_ids = {row["id"] for row in tx.query("SELECT id FROM workspaces")}
            if any(by_workspace.get(workspace_id, set()) != expected_roles for workspace_id in workspace_ids):
                raise CatalogDriftError("built-in role definition drift detected")
            declared_permissions: dict[str, tuple[str, bool]] = {}
            for raw, manifest in records:
                payload = manifest.canonical_payload(raw)
                serialized = canonical_json(payload)
                checksum = manifest_checksum(payload)
                existing = tx.execute(
                    "SELECT app.*, extension.manifest_json FROM apps AS app "
                    "LEFT JOIN app_manifests AS extension ON extension.app_id=app.id WHERE app.key=?",
                    (manifest.key,),
                ).fetchone()
                if existing is None:
                    app_id = str(uuid5(NAMESPACE_URL, f"my-workspace:foundation-app:{manifest.key}"))
                    tx.execute(
                        "INSERT INTO apps VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)",
                        (app_id, manifest.key, raw["display_name"], raw["description"], raw["icon"], raw["category"], raw["version"], manifest.status, checksum, stamp, stamp),
                    )
                    existing = tx.execute(
                        "SELECT app.*, NULL AS manifest_json FROM apps AS app WHERE app.id=?",
                        (app_id,),
                    ).fetchone()
                if existing["manifest_json"] is not None:
                    stored = json.loads(existing["manifest_json"])
                    if existing["manifest_checksum"] != manifest_checksum(stored):
                        raise CatalogDriftError(f"catalog checksum drift detected: {manifest.key}")
                    expected_columns = (
                        stored["display_name"], stored["description"], stored["icon"],
                        stored["category"], int(stored["platform"]["builtin"]),
                        stored["version"], stored["platform"]["status"],
                    )
                    actual_columns = (
                        existing["name"], existing["description"], existing["icon"],
                        existing["category"], existing["builtin"], existing["version"],
                        existing["status"],
                    )
                    if actual_columns != expected_columns:
                        raise CatalogDriftError(f"catalog metadata drift detected: {manifest.key}")
                for policy in tx.query(
                    "SELECT workspace_id, settings_json FROM workspace_apps WHERE app_id=?",
                    (existing["id"],),
                ):
                    try:
                        validate_settings(manifest.settings_schema, json.loads(policy["settings_json"]))
                    except (SettingsSchemaError, ValueError, TypeError) as error:
                        raise CatalogDriftError(
                            f"workspace settings drift detected: {manifest.key}/{policy['workspace_id']}"
                        ) from error
                tx.execute(
                    "UPDATE apps SET name=?, description=?, icon=?, category=?, builtin=?, version=?, status=?, "
                    "manifest_checksum=?, updated_at=? WHERE id=?",
                    (raw["display_name"], raw["description"], raw["icon"], raw["category"], int(manifest.builtin), raw["version"], manifest.status, checksum, stamp, existing["id"]),
                )
                tx.execute(
                    "INSERT INTO app_manifests(app_id, manifest_json, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(app_id) DO UPDATE SET manifest_json=excluded.manifest_json, updated_at=excluded.updated_at",
                    (existing["id"], serialized, stamp),
                )
                tx.execute(
                    "INSERT OR IGNORE INTO workspace_apps(workspace_id, app_id, enabled, display_order, settings_json, updated_by, created_at, updated_at) "
                    "SELECT workspace.id, ?, CASE workspace.type WHEN 'PERSONAL' THEN ? ELSE ? END, ?, '{}', workspace.created_by, ?, ? "
                    "FROM workspaces AS workspace",
                    (existing["id"], int(manifest.default_enabled.personal), int(manifest.default_enabled.team), manifest.sidebar.order, stamp, stamp),
                )
                for permission in manifest.permissions:
                    declared_permissions[permission.code] = (
                        manifest.key,
                        permission.owner_only,
                    )
                    permission_row = tx.execute(
                        "SELECT app_id FROM permissions WHERE code=?",
                        (permission.code,),
                    ).fetchone()
                    if permission_row is not None and permission_row["app_id"] != existing["id"]:
                        raise CatalogDriftError(
                            f"permission app association drift detected: {permission.code}"
                        )
                    tx.execute(
                        "INSERT INTO permissions(id, code, app_id, name, description, owner_only, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(code) DO UPDATE SET "
                        "name=excluded.name, description=excluded.description, owner_only=excluded.owner_only, updated_at=excluded.updated_at",
                        (permission_id(permission.code), permission.code, existing["id"], permission.name, permission.description, int(permission.owner_only), stamp, stamp),
                    )
            # Built-in role definitions are part of the manifest projection,
            # not a one-time signup seed. Reconcile every workspace so a newly
            # declared permission takes effect without stale role bindings.
            for role in tx.query(
                "SELECT id, system_role FROM roles WHERE builtin=1 AND status='ACTIVE'"
            ):
                role_key = role["system_role"].lower()
                desired: set[str] = set()
                for code, (app_key, owner_only) in declared_permissions.items():
                    suffix = code.removeprefix(f"{app_key}.")
                    if role_key == "owner":
                        desired.add(code)
                    elif role_key == "admin" and not owner_only:
                        desired.add(code)
                    elif role_key == "member" and app_key != "workspace" and suffix in {"read_all", "create", "update_own", "delete_own"}:
                        desired.add(code)
                    elif role_key == "viewer" and app_key != "workspace" and suffix == "read_all":
                        desired.add(code)
                tx.execute(
                    "DELETE FROM role_permissions WHERE role_id=? AND permission_id IN ("
                    "SELECT id FROM permissions WHERE code NOT IN ("
                    + ",".join("?" for _ in desired)
                    + "))",
                    (role["id"], *sorted(desired)),
                ) if desired else tx.execute(
                    "DELETE FROM role_permissions WHERE role_id=?", (role["id"],)
                )
                tx.executemany(
                    "INSERT OR IGNORE INTO role_permissions(role_id, permission_id, effect, created_at) VALUES (?, ?, 'ALLOW', ?)",
                    ((role["id"], permission_id(code), stamp) for code in sorted(desired)),
                )
            stale_permissions = tx.query(
                "SELECT permission.code FROM permissions AS permission "
                "JOIN apps AS app ON app.id=permission.app_id "
                "WHERE app.key IN (" + ",".join("?" for _ in desired_keys) + ") "
                "AND permission.code NOT IN (" + ",".join("?" for _ in declared_permissions) + ")",
                (*sorted(desired_keys), *sorted(declared_permissions)),
            )
            if stale_permissions:
                raise CatalogDriftError(
                    "undeclared permission drift detected: "
                    + ", ".join(sorted(row["code"] for row in stale_permissions))
                )
            tx.execute(
                "UPDATE apps SET status='DEPRECATED', updated_at=? WHERE builtin=1 AND key NOT IN ("
                + ",".join("?" for _ in desired_keys)
                + ")",
                (stamp, *sorted(desired_keys)),
            )
        return self.snapshot()

    def snapshot(self) -> AppCatalogSnapshot:
        records = load_builtin_manifest_records(self.manifest_path) if self.manifest_path else load_builtin_manifest_records()
        app_checksums: list[tuple[str, str]] = []
        permissions: list[str] = []
        widgets: list[tuple[str, str]] = []
        for raw, manifest in records:
            payload = manifest.canonical_payload(raw)
            checksum = manifest_checksum(payload)
            app_checksums.append((manifest.key, checksum))
            permissions.extend(permission.code for permission in manifest.permissions)
            widgets.extend((widget.key, manifest_checksum(widget.model_dump(mode="json"))) for widget in manifest.widgets)
        return AppCatalogSnapshot(tuple(sorted(app_checksums)), tuple(sorted(permissions)), tuple(sorted(widgets)))
