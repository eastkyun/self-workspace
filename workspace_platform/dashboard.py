"""Permission-aware dashboard widget catalog, layout, and projection service."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from access.context import PermissionSnapshot, WorkspaceContext
from access.errors import AuthorizationError
from identity.database import database_connection

from .app_manifests import canonical_json, manifest_checksum
from .settings_schema import SettingsSchemaError, validate_schema, validate_settings


_WIDGET_KEY = re.compile(r"^[a-z][a-z0-9_.-]{0,127}$")
_SIZES = frozenset({"SMALL", "MEDIUM", "LARGE"})


class DashboardError(RuntimeError):
    """Base error for dashboard catalog and layout operations."""


class DashboardCatalogError(DashboardError):
    """Manifest catalog and executable providers do not agree."""


class DashboardLayoutError(DashboardError, ValueError):
    """A saved dashboard layout is invalid."""


@runtime_checkable
class DashboardWidgetProvider(Protocol):
    widget_key: str
    app_key: str
    display_name: str
    required_permissions: tuple[str, ...]
    default_size: str
    settings_schema: dict[str, Any]

    def load(
        self, context: WorkspaceContext, settings: Mapping[str, Any], limit: int
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class DashboardLayoutItem:
    widget_key: str
    display_order: int
    size: str
    settings: Mapping[str, Any]
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class DashboardWidgetProjection:
    widget_key: str
    display_name: str
    size: str
    settings: Mapping[str, Any]
    data: object | None = None
    error: str | None = None


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


class DashboardWidgetRegistry:
    """Explicit runtime registry; manifests never import or execute provider code."""

    def __init__(self, providers: Iterable[DashboardWidgetProvider] = ()) -> None:
        ordered: list[DashboardWidgetProvider] = []
        by_key: dict[str, DashboardWidgetProvider] = {}
        for provider in providers:
            self._validate_provider(provider)
            if provider.widget_key in by_key:
                raise DashboardCatalogError(f"duplicate widget provider: {provider.widget_key}")
            by_key[provider.widget_key] = provider
            ordered.append(provider)
        self._ordered = tuple(ordered)
        self._by_key = by_key

    @staticmethod
    def _validate_provider(provider: DashboardWidgetProvider) -> None:
        if not _WIDGET_KEY.fullmatch(provider.widget_key):
            raise DashboardCatalogError("invalid widget key")
        if not re.fullmatch(r"[a-z][a-z0-9-]*", provider.app_key):
            raise DashboardCatalogError("invalid widget app key")
        if not provider.display_name.strip() or len(provider.display_name) > 80:
            raise DashboardCatalogError("invalid widget display name")
        if provider.default_size not in _SIZES:
            raise DashboardCatalogError("invalid widget default size")
        if len(provider.required_permissions) != len(set(provider.required_permissions)):
            raise DashboardCatalogError("widget permissions must be unique")
        validate_schema(provider.settings_schema)
        try:
            validate_settings(
                provider.settings_schema, dict(getattr(provider, "default_settings", {}))
            )
        except (SettingsSchemaError, TypeError, ValueError) as error:
            raise DashboardCatalogError("invalid widget default settings") from error
        workspace_types = tuple(
            getattr(provider, "default_workspace_types", ("PERSONAL", "TEAM"))
        )
        if not set(workspace_types).issubset({"PERSONAL", "TEAM"}):
            raise DashboardCatalogError("invalid widget default workspace types")

    def get(self, widget_key: str) -> DashboardWidgetProvider | None:
        return self._by_key.get(widget_key)

    def __iter__(self):
        return iter(self._ordered)

    def sync_catalog(self, database_path: str | Path) -> tuple[str, ...]:
        """Seed the durable catalog after the app manifest catalog has been synced."""
        stamp = _stamp()
        declared: dict[str, tuple[str, str, dict[str, Any]]] = {}
        with database_connection(database_path) as tx:
            for row in tx.query(
                "SELECT app.id AS app_id, app.key AS app_key, extension.manifest_json "
                "FROM apps AS app JOIN app_manifests AS extension ON extension.app_id=app.id"
            ):
                payload = json.loads(row["manifest_json"])
                for widget in payload["platform"].get("widgets", []):
                    key = widget["key"]
                    if key in declared:
                        raise DashboardCatalogError(f"duplicate manifest widget: {key}")
                    declared[key] = (row["app_id"], row["app_key"], widget)

            if set(declared) != set(self._by_key):
                missing = sorted(set(declared) - set(self._by_key))
                extra = sorted(set(self._by_key) - set(declared))
                raise DashboardCatalogError(
                    f"widget runtime wiring mismatch (missing={missing}, undeclared={extra})"
                )

            for key, (app_id, app_key, manifest) in declared.items():
                provider = self._by_key[key]
                if (
                    provider.app_key != app_key
                    or provider.display_name != manifest["name"]
                    or provider.required_permissions != tuple(manifest.get("required_permissions", ()))
                    or provider.default_size != manifest.get("default_size", "MEDIUM")
                    or provider.settings_schema != manifest["settings_schema"]
                ):
                    raise DashboardCatalogError(f"widget manifest drift: {key}")
                declared_permissions = {
                    row["code"] for row in tx.query(
                        "SELECT code FROM permissions WHERE app_id=?", (app_id,)
                    )
                }
                if not set(provider.required_permissions).issubset(declared_permissions):
                    raise DashboardCatalogError(f"widget permission drift: {key}")
                tx.execute(
                    "INSERT INTO dashboard_widgets(key,app_id,manifest_checksum,status,updated_at) "
                    "VALUES (?, ?, ?, 'ACTIVE', ?) ON CONFLICT(key) DO UPDATE SET "
                    "app_id=excluded.app_id,manifest_checksum=excluded.manifest_checksum,"
                    "status='ACTIVE',updated_at=excluded.updated_at",
                    (key, app_id, manifest_checksum(manifest), stamp),
                )
            if declared:
                placeholders = ",".join("?" for _ in declared)
                tx.execute(
                    f"UPDATE dashboard_widgets SET status='DISABLED', updated_at=? "
                    f"WHERE key NOT IN ({placeholders})",
                    (stamp, *sorted(declared)),
                )
            else:
                tx.execute("UPDATE dashboard_widgets SET status='DISABLED', updated_at=?", (stamp,))
        return tuple(provider.widget_key for provider in self._ordered)


class DashboardService:
    """Persists layouts and isolates non-authorization provider failures."""

    def __init__(
        self,
        database_path: str | Path,
        authorization: object,
        registry: DashboardWidgetRegistry,
        *,
        app_allowed: Callable[[WorkspaceContext, str, PermissionSnapshot], bool] | None = None,
        permission_allowed: Callable[[WorkspaceContext, str, PermissionSnapshot], bool] | None = None,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.authorization = authorization
        self.registry = registry
        self._app_allowed = app_allowed
        self._permission_allowed = permission_allowed

    def _snapshot(self, context: WorkspaceContext) -> PermissionSnapshot:
        return self.authorization.permission_snapshot(context)

    def _can_show(
        self, context: WorkspaceContext, provider: DashboardWidgetProvider,
        snapshot: PermissionSnapshot,
    ) -> bool:
        app_ok = (
            self._app_allowed(context, provider.app_key, snapshot)
            if self._app_allowed else
            self.authorization.authorize_app(context, provider.app_key, snapshot=snapshot)
        )
        if not app_ok:
            return False
        for code in provider.required_permissions:
            allowed = (
                self._permission_allowed(context, code, snapshot)
                if self._permission_allowed else
                self.authorization.authorize(context, code, snapshot=snapshot)
            )
            if not allowed:
                return False
        return True

    @staticmethod
    def _provider_defaults(provider: DashboardWidgetProvider, workspace_type: str) -> bool:
        types = getattr(provider, "default_workspace_types", ("PERSONAL", "TEAM"))
        return workspace_type in types

    def layout(self, context: WorkspaceContext) -> tuple[DashboardLayoutItem, ...]:
        # Resolving the snapshot first makes stale/cross-workspace contexts fail closed.
        self._snapshot(context)
        with database_connection(self.database_path) as tx:
            workspace = tx.execute(
                "SELECT type FROM workspaces WHERE id=?", (context.workspace_id,)
            ).fetchone()
            rows = tx.query(
                "SELECT widget_key,enabled,display_order,size,settings_json "
                "FROM user_dashboard_widgets WHERE user_id=? AND workspace_id=? "
                "ORDER BY display_order, widget_key",
                (context.user_id, context.workspace_id),
            )
        if rows:
            return tuple(
                DashboardLayoutItem(
                    row["widget_key"], row["display_order"], row["size"],
                    json.loads(row["settings_json"]), bool(row["enabled"]),
                )
                for row in rows
            )
        workspace_type = workspace["type"] if workspace is not None else ""
        return tuple(
            DashboardLayoutItem(
                provider.widget_key, order, provider.default_size,
                dict(getattr(provider, "default_settings", {})),
            )
            for order, provider in enumerate(self.registry)
            if self._provider_defaults(provider, workspace_type)
        )

    def save_layout(
        self, context: WorkspaceContext, items: Iterable[DashboardLayoutItem]
    ) -> tuple[DashboardLayoutItem, ...]:
        self._snapshot(context)
        requested = tuple(items)
        keys = [item.widget_key for item in requested]
        if len(keys) != len(set(keys)):
            raise DashboardLayoutError("widget keys must be unique")
        if len(requested) > 100:
            raise DashboardLayoutError("dashboard layout has too many widgets")
        normalized: list[DashboardLayoutItem] = []
        for item in requested:
            provider = self.registry.get(item.widget_key)
            if provider is None:
                raise DashboardLayoutError(f"unknown widget: {item.widget_key}")
            if item.display_order < 0 or item.display_order > 9999:
                raise DashboardLayoutError("invalid widget display order")
            if item.size not in _SIZES:
                raise DashboardLayoutError("invalid widget size")
            try:
                validate_settings(provider.settings_schema, dict(item.settings))
            except (SettingsSchemaError, TypeError, ValueError) as error:
                raise DashboardLayoutError(f"invalid widget settings: {item.widget_key}") from error
            normalized.append(
                DashboardLayoutItem(item.widget_key, item.display_order, item.size,
                                    dict(item.settings), bool(item.enabled))
            )
        stamp = _stamp()
        with database_connection(self.database_path) as tx:
            known = {
                row["key"] for row in tx.query(
                    "SELECT key FROM dashboard_widgets WHERE status='ACTIVE'"
                )
            }
            if not set(keys).issubset(known):
                raise DashboardLayoutError("layout contains an inactive widget")
            tx.execute(
                "UPDATE user_dashboard_widgets SET enabled=0,updated_at=? "
                "WHERE user_id=? AND workspace_id=?",
                (stamp, context.user_id, context.workspace_id),
            )
            tx.executemany(
                "INSERT INTO user_dashboard_widgets(user_id,workspace_id,widget_key,enabled,"
                "display_order,size,settings_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(user_id,workspace_id,widget_key) DO UPDATE SET "
                "enabled=excluded.enabled,display_order=excluded.display_order,size=excluded.size,"
                "settings_json=excluded.settings_json,updated_at=excluded.updated_at",
                (
                    (context.user_id, context.workspace_id, item.widget_key, int(item.enabled),
                     item.display_order, item.size, canonical_json(dict(item.settings)), stamp, stamp)
                    for item in normalized
                ),
            )
        return self.layout(context)

    def load(
        self,
        context: WorkspaceContext,
        *,
        limit: int = 20,
        layout: Iterable[DashboardLayoutItem] | None = None,
    ) -> tuple[DashboardWidgetProjection, ...]:
        snapshot = self._snapshot(context)
        result: list[DashboardWidgetProjection] = []
        items = tuple(layout) if layout is not None else self.layout(context)
        for item in items:
            provider = self.registry.get(item.widget_key)
            if not item.enabled or provider is None or not self._can_show(context, provider, snapshot):
                continue
            try:
                data = provider.load(context, item.settings, min(max(limit, 1), 100))
                result.append(DashboardWidgetProjection(
                    item.widget_key, provider.display_name, item.size, item.settings, data=data
                ))
            except AuthorizationError:
                raise
            except Exception:
                result.append(DashboardWidgetProjection(
                    item.widget_key, provider.display_name, item.size, item.settings,
                    error="provider_error",
                ))
        return tuple(result)
