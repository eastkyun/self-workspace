from __future__ import annotations

import json
import sqlite3
from copy import deepcopy

import pytest

from access.service import AuthorizationService
from identity.auth import AuthenticationService
from identity.database import IDENTITY_SCHEMA_VERSION, MIGRATIONS, initialize_database
from identity.notifications import MemoryNotificationProvider
from identity.foundation import permission_id
from identity.passwords import CHEAPEST_TEST_PASSWORD_HASHER
from shared.app_registry import REGISTRY_PATH
from shared.migrations import migrate
from workspace_platform.app_catalog import AppCatalogService, CatalogDriftError
from workspace_platform.app_manifests import load_builtin_manifests
from workspace_platform.app_policy import AppPolicyConflict, AppPolicyNotFound, WorkspaceAppPolicyService
from identity.workspaces import WorkspaceService


MANIFEST_KEYS = frozenset({"home", "notes", "calendar", "search", "publishing", "assets", "workspace", "jobs"})
RUNTIME_KEYS = frozenset({"home", "notes", "calendar", "search", "publishing", "assets", "workspace", "jobs"})


def _seed(tmp_path):
    database = initialize_database(tmp_path / "identity.db")
    auth = AuthenticationService(database, notification_provider=MemoryNotificationProvider(), public_origin="http://testserver", hasher=CHEAPEST_TEST_PASSWORD_HASHER)
    credentials = auth.signup("owner@example.com", "password", "Owner")
    user = auth.authenticate(credentials.session_token)
    return database, user, credentials.workspace_id


def test_manifest_is_declarative_and_catalog_sync_is_idempotent_and_drift_safe(tmp_path):
    database, _, _ = _seed(tmp_path)
    manifests = load_builtin_manifests()
    assert {item.key for item in manifests} == MANIFEST_KEYS
    assert all(not hasattr(item, "module") for item in manifests)
    service = AppCatalogService(database)
    first = service.sync(runtime_app_keys=RUNTIME_KEYS)
    second = service.sync(runtime_app_keys=RUNTIME_KEYS)
    assert first == second
    assert first.permission_codes
    assert {key for key, _ in first.widget_checksums} == {
        "calendar.today",
        "favorites",
        "notes.mine",
        "notes.recent",
        "recent_views",
        "workspace.activity",
        "workspace.search",
    }
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE apps SET name='Tampered' WHERE key='notes'")
    with pytest.raises(CatalogDriftError, match="metadata drift"):
        service.sync(runtime_app_keys=RUNTIME_KEYS)
    with pytest.raises(CatalogDriftError, match="runtime wiring"):
        AppCatalogService(database).sync(runtime_app_keys=RUNTIME_KEYS - {"calendar"})


def test_workspace_settings_validation_home_invariant_and_catalog_fk(tmp_path):
    database, user, workspace_id = _seed(tmp_path)
    AppCatalogService(database).sync(runtime_app_keys=RUNTIME_KEYS)
    authorization = AuthorizationService(database)
    policy = WorkspaceAppPolicyService(database, authorization)
    with pytest.raises(AppPolicyConflict, match="HOME"):
        policy.update_app(user.id, workspace_id, "home", enabled=False, display_order=None, settings=None)
    with pytest.raises(ValueError, match="unknown properties"):
        policy.update_app(user.id, workspace_id, "notes", enabled=None, display_order=None, settings={"unknown": True})
    policy.update_app(user.id, workspace_id, "publishing", enabled=True, display_order=5, settings={})
    context = authorization.resolve_context(user.id, workspace_id)
    apps = {item["key"]: item for item in policy.list_apps(context)}
    assert apps["publishing"]["enabled"] is True
    assert apps["publishing"]["effective"] is True
    policy.update_app(user.id, workspace_id, "notes", enabled=False, display_order=None, settings=None)
    disabled_context = authorization.resolve_context(user.id, workspace_id)
    with pytest.raises(AppPolicyNotFound):
        policy.require_app(disabled_context, "notes")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_member_permission_override_invalidates_snapshot_and_protects_owner(tmp_path):
    database = initialize_database(tmp_path / "identity.db")
    notifications = MemoryNotificationProvider()
    auth = AuthenticationService(database, notification_provider=notifications, public_origin="http://testserver", hasher=CHEAPEST_TEST_PASSWORD_HASHER)
    owner_credentials = auth.signup("owner@example.com", "password", "Owner")
    owner = auth.authenticate(owner_credentials.session_token)
    member_credentials = auth.signup("member@example.com", "password", "Member")
    member = auth.authenticate(member_credentials.session_token)
    workspaces = WorkspaceService(database, notification_provider=notifications, public_origin="http://testserver")
    team = workspaces.create_team(owner, "Team")
    workspaces.invite(owner, team["id"], member.email, "member")
    token = notifications.invitation_messages[-1].invitation_url.rsplit("/", 1)[1]
    accepted = workspaces.accept_invitation(member, token)
    AppCatalogService(database).sync(runtime_app_keys=RUNTIME_KEYS)
    authorization = AuthorizationService(database)
    policy = WorkspaceAppPolicyService(database, authorization)
    before = authorization.resolve_context(member.id, team["id"])
    assert authorization.authorize(before, "notes.create")
    policy.set_permission_override(owner.id, team["id"], accepted["membership_id"], "notes.create", "DENY")
    after = authorization.resolve_context(member.id, team["id"])
    assert after.authorization_version > before.authorization_version
    assert not authorization.authorize(after, "notes.create")
    policy.clear_permission_override(owner.id, team["id"], accepted["membership_id"], "notes.create")
    restored = authorization.resolve_context(member.id, team["id"])
    assert authorization.authorize(restored, "notes.create")
    with pytest.raises(AppPolicyConflict, match="Owner"):
        policy.set_permission_override(owner.id, team["id"], team["membership_id"], "notes.create", "DENY")
    with pytest.raises(AppPolicyConflict, match="Owner-only"):
        policy.set_permission_override(owner.id, team["id"], accepted["membership_id"], "workspace.delete", "ALLOW")


def test_adjacent_v2_to_v4_and_fresh_zero_to_four_are_repeatable(tmp_path):
    adjacent = tmp_path / "adjacent.db"
    with sqlite3.connect(adjacent) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        migrate(connection, target_version=2, migrations=MIGRATIONS)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    initialize_database(adjacent)
    initialize_database(adjacent)
    with sqlite3.connect(adjacent) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == IDENTITY_SCHEMA_VERSION
        assert connection.execute("SELECT COUNT(*) FROM app_manifests").fetchone()[0] == 0

    fresh = initialize_database(tmp_path / "fresh.db")
    with sqlite3.connect(fresh) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == IDENTITY_SCHEMA_VERSION
        trigger_names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'authz_version_%'")}
        assert len(trigger_names) == 17


def test_manifest_rejects_runtime_code_fields(tmp_path):
    payload = json.loads(REGISTRY_PATH.read_text())
    payload["apps"][0]["platform"]["module"] = "evil.plugin"
    source = tmp_path / "apps.json"
    source.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="Extra inputs"):
        load_builtin_manifests(source)


@pytest.mark.parametrize("route", ["javascript:alert(1)", "https://evil.example/x", "//evil.example/x", "/w/{workspace_id}/../secret", "/w/{workspace_id}/./notes", "/w/{workspace_id}//notes", "/w/{workspace_id}/bad path", "/w/{workspace_id}/notes?token=x", "/w/%7Bworkspace_id%7D/notes"])
def test_manifest_rejects_unsafe_ui_routes(tmp_path, route):
    payload = json.loads(REGISTRY_PATH.read_text())
    payload["apps"][0]["platform"]["routes"]["ui"] = route
    source = tmp_path / "unsafe-route.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="canonical same-origin"):
        load_builtin_manifests(source)


def test_catalog_sync_reconciles_new_permission_into_builtin_roles(tmp_path):
    database, _, workspace_id = _seed(tmp_path)
    AppCatalogService(database).sync(runtime_app_keys=RUNTIME_KEYS)
    payload = json.loads(REGISTRY_PATH.read_text())
    notes = next(item for item in payload["apps"] if item["id"] == "notes")
    notes["platform"]["permissions"].append(
        {
            "code": "notes.export",
            "name": "Export notes",
            "description": "Export workspace notes",
            "supports_own": False,
            "owner_only": False,
        }
    )
    source = tmp_path / "apps-with-export.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    AppCatalogService(database, manifest_path=source).sync(runtime_app_keys=RUNTIME_KEYS)
    with sqlite3.connect(database) as connection:
        bound_roles = {
            row[0]
            for row in connection.execute(
                "SELECT role.key FROM role_permissions AS binding "
                "JOIN roles AS role ON role.id=binding.role_id "
                "JOIN permissions AS permission ON permission.id=binding.permission_id "
                "WHERE role.workspace_id=? AND permission.code='notes.export'",
                (workspace_id,),
            )
        }
    assert bound_roles == {"owner", "admin"}


def test_builtin_role_matrix_matches_fixed_contract(tmp_path):
    database, _, workspace_id = _seed(tmp_path)
    AppCatalogService(database).sync(runtime_app_keys=RUNTIME_KEYS)
    manifests = load_builtin_manifests()
    all_codes = {permission.code for app in manifests for permission in app.permissions}
    owner_only = {permission.code for app in manifests for permission in app.permissions if permission.owner_only}
    expected = {
        "owner": all_codes,
        "admin": all_codes - owner_only,
        "member": {
            permission.code
            for app in manifests if app.key != "workspace"
            for permission in app.permissions
            if permission.code.rsplit(".", 1)[-1] in {"read_all", "create", "update_own", "delete_own"}
        },
        "viewer": {
            permission.code
            for app in manifests if app.key != "workspace"
            for permission in app.permissions
            if permission.code.endswith(".read_all")
        },
    }
    with sqlite3.connect(database) as connection:
        for role_key, desired in expected.items():
            actual = {
                row[0]
                for row in connection.execute(
                    "SELECT permission.code FROM role_permissions AS binding "
                    "JOIN roles AS role ON role.id=binding.role_id "
                    "JOIN permissions AS permission ON permission.id=binding.permission_id "
                    "WHERE role.workspace_id=? AND role.key=?",
                    (workspace_id, role_key),
                )
            }
            assert actual == desired


def test_builtin_role_definition_drift_fails_before_catalog_reconciliation(tmp_path):
    database, _, workspace_id = _seed(tmp_path)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE roles SET key='boss' WHERE workspace_id=? AND system_role='OWNER'",
            (workspace_id,),
        )
    with pytest.raises(CatalogDriftError, match="built-in role definition drift"):
        AppCatalogService(database).sync(runtime_app_keys=RUNTIME_KEYS)
    with sqlite3.connect(database) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM role_permissions AS binding JOIN roles AS role "
            "ON role.id=binding.role_id WHERE role.workspace_id=? AND role.system_role='OWNER'",
            (workspace_id,),
        ).fetchone()[0]
    assert count > 0


def test_catalog_rejects_undeclared_permission_drift(tmp_path):
    database, _, _ = _seed(tmp_path)
    service = AppCatalogService(database)
    service.sync(runtime_app_keys=RUNTIME_KEYS)
    with sqlite3.connect(database) as connection:
        notes_id = connection.execute("SELECT id FROM apps WHERE key='notes'").fetchone()[0]
        connection.execute(
            "INSERT INTO permissions VALUES (?, 'notes.shadow', ?, 'Shadow', '', 0, '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z')",
            (permission_id("notes.shadow"), notes_id),
        )
    with pytest.raises(CatalogDriftError, match="undeclared permission drift"):
        service.sync(runtime_app_keys=RUNTIME_KEYS)


def test_catalog_rejects_permission_app_association_drift(tmp_path):
    database, _, _ = _seed(tmp_path)
    service = AppCatalogService(database)
    service.sync(runtime_app_keys=RUNTIME_KEYS)
    with sqlite3.connect(database) as connection:
        calendar_id = connection.execute("SELECT id FROM apps WHERE key='calendar'").fetchone()[0]
        connection.execute("UPDATE permissions SET app_id=? WHERE code='notes.read_all'", (calendar_id,))
    with pytest.raises(CatalogDriftError, match="association drift"):
        service.sync(runtime_app_keys=RUNTIME_KEYS)


def test_new_manifest_app_is_seeded_for_existing_workspaces_and_removal_deprecates(tmp_path):
    database, _, workspace_id = _seed(tmp_path)
    payload = json.loads(REGISTRY_PATH.read_text())
    manual = deepcopy(next(item for item in payload["apps"] if item["id"] == "home"))
    manual.update({"id": "manual", "name": "manual", "display_name": "Manual", "route": "/apps/manual/"})
    manual["platform"].update({"key": "manual", "routes": {"ui": "/w/{workspace_id}/manual", "api_v1": None, "public": None}, "default_enabled": {"personal": False, "team": False}})
    manual["platform"]["widgets"] = []
    manual["platform"]["capabilities"]["dashboard"] = False
    payload["apps"].append(manual)
    source = tmp_path / "apps-with-manual.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    expanded_keys = RUNTIME_KEYS | {"manual"}
    AppCatalogService(database, manifest_path=source).sync(runtime_app_keys=expanded_keys)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT app.status, policy.enabled FROM apps AS app JOIN workspace_apps AS policy ON policy.app_id=app.id WHERE app.key='manual' AND policy.workspace_id=?",
            (workspace_id,),
        ).fetchone()
    assert row == ("ACTIVE", 0)
    AppCatalogService(database).sync(runtime_app_keys=RUNTIME_KEYS)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM apps WHERE key='manual'").fetchone()[0] == "DEPRECATED"
        assert connection.execute("SELECT COUNT(*) FROM workspace_apps AS policy JOIN apps AS app ON app.id=policy.app_id WHERE app.key='manual' AND policy.workspace_id=?", (workspace_id,)).fetchone()[0] == 1


def test_catalog_rejects_existing_settings_invalidated_by_new_manifest_schema(tmp_path):
    database, _, workspace_id = _seed(tmp_path)
    payload = json.loads(REGISTRY_PATH.read_text())
    notes = next(item for item in payload["apps"] if item["id"] == "notes")
    notes["platform"]["settings_schema"] = {
        "type": "object",
        "properties": {"color": {"type": "string"}},
        "additionalProperties": False,
    }
    source = tmp_path / "apps-color.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    AppCatalogService(database, manifest_path=source).sync(runtime_app_keys=RUNTIME_KEYS)
    with sqlite3.connect(database) as connection:
        notes_id = connection.execute("SELECT id FROM apps WHERE key='notes'").fetchone()[0]
        connection.execute(
            "UPDATE workspace_apps SET settings_json='{\"color\":\"red\"}' "
            "WHERE workspace_id=? AND app_id=?",
            (workspace_id, notes_id),
        )
    with pytest.raises(CatalogDriftError, match="workspace settings drift"):
        AppCatalogService(database).sync(runtime_app_keys=RUNTIME_KEYS)
