from __future__ import annotations

import json
import sqlite3
from copy import deepcopy
from dataclasses import dataclass

import pytest

from access.errors import PermissionDenied
from access.service import AuthorizationService
from identity.auth import AuthenticationService
from identity.database import IDENTITY_SCHEMA_VERSION, initialize_database
from identity.notifications import MemoryNotificationProvider
from identity.passwords import CHEAPEST_TEST_PASSWORD_HASHER
from shared.app_registry import REGISTRY_PATH
from workspace_platform.app_catalog import AppCatalogService
from workspace_platform.app_policy import WorkspaceAppPolicyService
from workspace_platform.dashboard import (
    DashboardCatalogError,
    DashboardLayoutError,
    DashboardLayoutItem,
    DashboardService,
    DashboardWidgetRegistry,
)


RUNTIME_KEYS = frozenset({"home", "notes", "calendar", "search", "publishing", "assets", "workspace"})
EMPTY_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}


@dataclass
class Provider:
    widget_key: str
    app_key: str
    display_name: str
    required_permissions: tuple[str, ...]
    default_size: str = "MEDIUM"
    settings_schema: dict = None  # type: ignore[assignment]
    result: object = None
    failure: Exception | None = None
    calls: int = 0

    def __post_init__(self):
        if self.settings_schema is None:
            self.settings_schema = deepcopy(EMPTY_SCHEMA)

    def load(self, context, settings, limit):
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.result if self.result is not None else {"limit": limit}


def _system(tmp_path, providers):
    manifest = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    by_key = {item["id"]: item for item in manifest["apps"]}
    for item in manifest["apps"]:
        item["platform"]["widgets"] = []
    for provider in providers:
        by_key[provider.app_key]["platform"]["widgets"].append(
            {
                "key": provider.widget_key,
                "name": provider.display_name,
                "required_permissions": list(provider.required_permissions),
                "default_size": provider.default_size,
                "settings_schema": provider.settings_schema,
            }
        )
    source = tmp_path / "apps.json"
    source.write_text(json.dumps(manifest), encoding="utf-8")
    database = initialize_database(tmp_path / "identity.db")
    authentication = AuthenticationService(
        database,
        notification_provider=MemoryNotificationProvider(),
        public_origin="http://testserver",
        hasher=CHEAPEST_TEST_PASSWORD_HASHER,
    )
    credentials = authentication.signup("owner@example.com", "password", "Owner")
    user = authentication.authenticate(credentials.session_token)
    AppCatalogService(database, manifest_path=source).sync(runtime_app_keys=RUNTIME_KEYS)
    authorization = AuthorizationService(database)
    context = authorization.resolve_context(user.id, credentials.workspace_id)
    registry = DashboardWidgetRegistry(providers)
    registry.sync_catalog(database)
    return database, user, context, authorization, registry


def test_widget_catalog_seeding_is_idempotent_and_rejects_runtime_drift(tmp_path):
    provider = Provider("notes.recent", "notes", "Recent notes", ("notes.read_all",))
    database, _, _, _, registry = _system(tmp_path, (provider,))
    assert registry.sync_catalog(database) == ("notes.recent",)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT key,status,length(manifest_checksum) FROM dashboard_widgets"
        ).fetchone()
        assert row == ("notes.recent", "ACTIVE", 64)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == IDENTITY_SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    bad = Provider("notes.recent", "calendar", "Recent notes", ("calendar.read_all",))
    with pytest.raises(DashboardCatalogError, match="manifest drift"):
        DashboardWidgetRegistry((bad,)).sync_catalog(database)
    with pytest.raises(DashboardCatalogError, match="runtime wiring"):
        DashboardWidgetRegistry().sync_catalog(database)


def test_layout_defaults_are_deterministic_validated_and_persisted_per_user_workspace(tmp_path):
    first = Provider("notes.recent", "notes", "Recent notes", ("notes.read_all",), "LARGE")
    second = Provider(
        "calendar.today", "calendar", "Today", ("calendar.read_all",), "SMALL",
        {"type": "object", "properties": {"days": {"type": "integer", "minimum": 1, "maximum": 7}}, "additionalProperties": False},
    )
    database, _, context, authorization, registry = _system(tmp_path, (first, second))
    service = DashboardService(database, authorization, registry)
    assert [(item.widget_key, item.display_order, item.size) for item in service.layout(context)] == [
        ("notes.recent", 0, "LARGE"), ("calendar.today", 1, "SMALL")
    ]
    saved = service.save_layout(context, (
        DashboardLayoutItem("calendar.today", 0, "MEDIUM", {"days": 3}),
    ))
    assert [(item.widget_key, item.enabled, dict(item.settings)) for item in saved] == [
        ("calendar.today", True, {"days": 3})
    ]
    assert service.load(context)[0].data == {"limit": 20}
    with pytest.raises(DashboardLayoutError, match="settings"):
        service.save_layout(context, (
            DashboardLayoutItem("calendar.today", 0, "MEDIUM", {"days": 99}),
        ))
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT settings_json FROM user_dashboard_widgets WHERE user_id=? AND workspace_id=?",
            (context.user_id, context.workspace_id),
        ).fetchone()[0] == '{"days":3}'


def test_load_reuses_a_preloaded_layout_without_querying_it_again(tmp_path, monkeypatch):
    provider = Provider("notes.recent", "notes", "Recent notes", ("notes.read_all",))
    database, _, context, authorization, registry = _system(tmp_path, (provider,))
    service = DashboardService(database, authorization, registry)
    layout = service.layout(context)

    monkeypatch.setattr(
        service,
        "layout",
        lambda _context: pytest.fail("preloaded layout should be reused"),
    )

    assert service.load(context, layout=layout)[0].widget_key == "notes.recent"


def test_render_filters_disabled_apps_and_permissions_without_deleting_layout(tmp_path):
    notes = Provider("notes.recent", "notes", "Recent notes", ("notes.read_all",), result=["note"])
    calendar = Provider("calendar.today", "calendar", "Today", ("calendar.read_all",), result=["event"])
    database, user, context, authorization, registry = _system(tmp_path, (notes, calendar))
    service = DashboardService(
        database, authorization, registry,
        permission_allowed=lambda _context, code, _snapshot: code != "calendar.read_all",
    )
    assert [item.widget_key for item in service.load(context)] == ["notes.recent"]
    assert calendar.calls == 0

    WorkspaceAppPolicyService(database, authorization).update_app(
        user.id, context.workspace_id, "notes", enabled=False,
        display_order=None, settings=None,
    )
    refreshed = authorization.resolve_context(user.id, context.workspace_id)
    assert service.load(refreshed) == ()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM user_dashboard_widgets WHERE user_id=? AND workspace_id=?",
            (context.user_id, context.workspace_id),
        ).fetchone()[0] == 0  # deterministic defaults are intentionally not materialized

    service.save_layout(refreshed, (
        DashboardLayoutItem("notes.recent", 0, "MEDIUM", {}),
        DashboardLayoutItem("calendar.today", 1, "MEDIUM", {}),
    ))
    assert service.load(refreshed) == ()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM user_dashboard_widgets WHERE user_id=? AND workspace_id=?",
            (context.user_id, context.workspace_id),
        ).fetchone()[0] == 2


def test_provider_failures_are_isolated_but_authorization_failures_propagate(tmp_path):
    healthy = Provider("notes.recent", "notes", "Recent notes", ("notes.read_all",), result=["ok"])
    broken = Provider("calendar.today", "calendar", "Today", ("calendar.read_all",), failure=RuntimeError("secret details"))
    database, _, context, authorization, registry = _system(tmp_path, (healthy, broken))
    result = DashboardService(database, authorization, registry).load(context)
    assert result[0].data == ["ok"]
    assert result[1].data is None
    assert result[1].error == "provider_error"
    assert "secret" not in repr(result[1])

    broken.failure = PermissionDenied("revoked during provider execution")
    with pytest.raises(PermissionDenied):
        DashboardService(database, authorization, registry).load(context)
