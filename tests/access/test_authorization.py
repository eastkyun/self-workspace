from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from access.context import PermissionSnapshot
from access.dependencies import require_app, require_permission
from access.policy import permission_allowed
from access.service import AuthorizationService
from access.errors import AppNotAvailable, WorkspaceNotAvailable
from shared.errors import APIError
from access.consumer_contract import AUTHORIZATION_CONSUMER_KEYS
from identity.auth import AuthenticationService
from identity.database import initialize_database
from identity.notifications import MemoryNotificationProvider
from identity.passwords import CHEAPEST_TEST_PASSWORD_HASHER
from identity.primitives import new_uuid
from workspace_platform.app_catalog import AppCatalogService
from workspace_platform.effective_apps import EffectiveWorkspaceApps


RUNTIME_KEYS = frozenset({"home", "notes", "calendar", "search", "publishing", "assets", "workspace", "jobs"})


def _system(tmp_path):
    database = initialize_database(tmp_path / "identity.db")
    auth = AuthenticationService(
        database,
        notification_provider=MemoryNotificationProvider(),
        public_origin="http://testserver",
        hasher=CHEAPEST_TEST_PASSWORD_HASHER,
    )
    owner_credentials = auth.signup("owner@example.com", "password", "Owner")
    member_credentials = auth.signup("member@example.com", "password", "Member")
    AppCatalogService(database).sync(runtime_app_keys=RUNTIME_KEYS)
    with sqlite3.connect(database) as connection:
        owner_workspace = owner_credentials.workspace_id
        member_role = connection.execute(
            "SELECT id FROM roles WHERE workspace_id=? AND key='member'", (owner_workspace,)
        ).fetchone()[0]
        membership_id = new_uuid()
        connection.execute(
            "INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z')",
            (membership_id, owner_workspace, member_credentials.user_id, member_role),
        )
    return database, owner_workspace, member_credentials.user_id, membership_id


def test_pure_policy_fixes_deny_allow_role_owner_only_and_own_all_order():
    snapshot = PermissionSnapshot(
        workspace_id="w", membership_id="m", role_key="member", version=1,
        enabled_apps=(("notes", "a"),),
        role_permissions=frozenset({"notes.read_all", "notes.update_own"}),
        explicit_allows=frozenset({"notes.update_all", "notes.delete_own"}),
        explicit_denies=frozenset({"notes.read_all", "notes.delete_own"}),
        owner_only_permissions=frozenset({"workspace.delete"}),
        read_required_apps=frozenset({"notes"}),
    )
    assert not permission_allowed(snapshot, "notes.read_all", user_id="u")
    assert permission_allowed(snapshot, "notes.update_all", user_id="u")
    assert permission_allowed(snapshot, "notes.update_own", user_id="u", resource_owner_id="u")
    assert not permission_allowed(snapshot, "notes.update_own", user_id="u", resource_owner_id="other")
    assert not permission_allowed(snapshot, "notes.delete_own", user_id="u", resource_owner_id="u")
    owner_only_granted = snapshot.__class__(
        **{**snapshot.__dict__, "explicit_allows": frozenset({"workspace.delete"})}
    ) if hasattr(snapshot, "__dict__") else PermissionSnapshot(
        "w", "m", "member", 1, (("notes", "a"),), frozenset(),
        frozenset({"workspace.delete"}), frozenset(), frozenset({"workspace.delete"})
    )
    assert not permission_allowed(owner_only_granted, "workspace.delete", user_id="u")

    permissionless = PermissionSnapshot(
        workspace_id="w", membership_id="custom", role_key="custom", version=1,
        enabled_apps=(("notes", "a"),), role_permissions=frozenset(),
        explicit_allows=frozenset(), explicit_denies=frozenset(),
        owner_only_permissions=frozenset(), read_required_apps=frozenset({"notes"}),
    )
    from access.policy import app_allowed
    assert not app_allowed(permissionless, "notes", user_id="u")


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("role_key", ["owner", "admin", "member", "viewer", "custom"])
@pytest.mark.parametrize("override", [None, "ALLOW", "DENY"])
def test_effective_app_access_matrix_enabled_role_and_override(enabled, role_key, override):
    from access.policy import app_allowed
    role_permissions = (
        frozenset({"notes.read_all"}) if role_key != "custom" else frozenset()
    )
    snapshot = PermissionSnapshot(
        workspace_id="w", membership_id="m", role_key=role_key, version=1,
        enabled_apps=(("notes", "a"),) if enabled else (),
        role_permissions=role_permissions,
        explicit_allows=frozenset({"notes.read_all"}) if override == "ALLOW" else frozenset(),
        explicit_denies=frozenset({"notes.read_all"}) if override == "DENY" else frozenset(),
        owner_only_permissions=frozenset(), read_required_apps=frozenset({"notes"}),
    )
    expected = enabled and override != "DENY" and (
        override == "ALLOW" or role_key != "custom"
    )
    assert app_allowed(snapshot, "notes", user_id="u") is expected


def test_next_request_snapshot_invalidation_and_shell_api_gate_parity(tmp_path):
    database, workspace_id, member_user_id, membership_id = _system(tmp_path)
    service = AuthorizationService(database)
    effective = EffectiveWorkspaceApps(database, service)
    context = service.resolve_context(member_user_id, workspace_id)
    first = service.permission_snapshot(context)
    assert service.authorize_app(context, "notes", snapshot=first)
    assert "notes" in {app.key for app in effective.list(context, snapshot=first)}

    with sqlite3.connect(database) as connection:
        permission_id = connection.execute(
            "SELECT id FROM permissions WHERE code='notes.read_all'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO member_permission_overrides VALUES (?, ?, ?, 'DENY', ?, '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z')",
            (workspace_id, membership_id, permission_id, member_user_id),
        )
    next_context = service.resolve_context(member_user_id, workspace_id)
    second = service.permission_snapshot(next_context)
    assert second.version > first.version
    assert not service.authorize_app(next_context, "notes", snapshot=second)
    assert "notes" not in {app.key for app in effective.list(next_context, snapshot=second)}

    with sqlite3.connect(database) as connection:
        notes_id = connection.execute("SELECT id FROM apps WHERE key='notes'").fetchone()[0]
        connection.execute(
            "UPDATE workspace_apps SET enabled=0 WHERE workspace_id=? AND app_id=?",
            (workspace_id, notes_id),
        )
    third_context = service.resolve_context(member_user_id, workspace_id)
    assert third_context.authorization_version > second.version
    assert not service.authorize_app(third_context, "notes")
    with pytest.raises(AppNotAvailable):
        service.require(third_context, app_key="notes")


def test_authorization_architecture_has_one_precedence_implementation():
    assert AUTHORIZATION_CONSUMER_KEYS == {
        "notes", "calendar", "publishing", "assets", "search", "dashboard",
        "internal-links", "ai-extension",
    }
    root = Path(__file__).resolve().parents[2]
    forbidden = ("member_permission_overrides", "explicit_denies", "DENY > ALLOW")
    offenders = []
    for directory in ("apps", "assets", "publishing", "dashboard"):
        target = root / directory
        if not target.exists():
            continue
        for source in target.rglob("*.py"):
            text = source.read_text(encoding="utf-8")
            if any(marker in text for marker in forbidden):
                offenders.append(str(source.relative_to(root)))
    assert offenders == []


def test_role_permission_update_bumps_authorization_version(tmp_path):
    database, workspace_id, member_user_id, _ = _system(tmp_path)
    service = AuthorizationService(database)
    before = service.resolve_context(member_user_id, workspace_id).authorization_version
    with sqlite3.connect(database) as connection:
        member_role = connection.execute("SELECT id FROM roles WHERE workspace_id=? AND key='member'", (workspace_id,)).fetchone()[0]
        viewer_role = connection.execute("SELECT id FROM roles WHERE workspace_id=? AND key='viewer'", (workspace_id,)).fetchone()[0]
        permission = connection.execute(
            "SELECT binding.permission_id FROM role_permissions AS binding JOIN permissions AS permission ON permission.id=binding.permission_id WHERE binding.role_id=? AND permission.code='notes.create'",
            (member_role,),
        ).fetchone()[0]
        connection.execute("UPDATE role_permissions SET role_id=? WHERE role_id=? AND permission_id=?", (viewer_role, member_role, permission))
    after = service.resolve_context(member_user_id, workspace_id).authorization_version
    assert after > before


def test_snapshot_is_bound_to_its_workspace_and_membership(tmp_path):
    database, workspace_id, member_user_id, _ = _system(tmp_path)
    service = AuthorizationService(database)
    member_context = service.resolve_context(member_user_id, workspace_id)
    with sqlite3.connect(database) as connection:
        owner_user_id = connection.execute(
            "SELECT user_id FROM workspace_members WHERE workspace_id=? AND role_id IN "
            "(SELECT id FROM roles WHERE workspace_id=? AND key='owner')",
            (workspace_id, workspace_id),
        ).fetchone()[0]
    owner_context = service.resolve_context(owner_user_id, workspace_id)
    snapshot = service.permission_snapshot(member_context)
    with pytest.raises(WorkspaceNotAvailable):
        service.authorize(owner_context, "notes.read_all", snapshot=snapshot)


def test_stale_workspace_context_maps_to_not_found_at_shared_dependencies(tmp_path):
    database, workspace_id, member_user_id, membership_id = _system(tmp_path)
    service = AuthorizationService(database)
    context = service.resolve_context(member_user_id, workspace_id)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE workspace_members SET status='LEFT' WHERE id=?", (membership_id,)
        )
    for dependency in (require_app(service, "notes"), require_permission(service, "notes.read_all")):
        with pytest.raises(APIError) as caught:
            dependency(context)
        assert caught.value.status_code == 404


def test_old_snapshot_is_rejected_by_new_authorization_version(tmp_path):
    database, workspace_id, member_user_id, membership_id = _system(tmp_path)
    service = AuthorizationService(database)
    old_context = service.resolve_context(member_user_id, workspace_id)
    old_snapshot = service.permission_snapshot(old_context)
    with sqlite3.connect(database) as connection:
        permission_id = connection.execute(
            "SELECT id FROM permissions WHERE code='notes.read_all'"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO member_permission_overrides VALUES (?, ?, ?, 'DENY', ?, '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z')",
            (workspace_id, membership_id, permission_id, member_user_id),
        )
    new_context = service.resolve_context(member_user_id, workspace_id)
    with pytest.raises(WorkspaceNotAvailable):
        service.authorize_app(new_context, "notes", snapshot=old_snapshot)
