from __future__ import annotations

import hashlib
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from identity.auth import AuthenticationService, SessionPolicy
from identity.database import database_connection, initialize_database
from identity.notifications import MemoryNotificationProvider
from identity.passwords import CHEAPEST_TEST_PASSWORD_HASHER
from identity.primitives import new_uuid
from identity.router import create_auth_router
from identity.workspace_router import create_workspace_router
from identity.workspace_shell import create_workspace_shell_router
from identity.workspaces import (
    WorkspaceConflict,
    WorkspaceForbidden,
    WorkspaceNotFound,
    WorkspaceService,
)
from shared.config import load_config
from shared.errors import install_exception_handlers
from shared.security_logging import AuthTokenRedactionFilter, redact_auth_tokens
from access.service import AuthorizationService
from workspace_platform.app_catalog import AppCatalogService
from workspace_platform.app_policy import WorkspaceAppPolicyService
from workspace_platform.effective_apps import EffectiveWorkspaceApps
from workspace_platform.user_state import WorkspaceUserStateService
from workspace_platform.user_state_resources import WorkspaceUserStateResourceResolver
from workspace_platform.user_state_router import create_user_state_router
from apps.notes.app.database import initialize_database as initialize_notes_database
from apps.notes.app.schemas import NoteCreate
from apps.notes.app.service import NotesService
from apps.calendar.app.main import initialize_workspace_database as initialize_calendar_database


class Clock:
    def __init__(self):
        self.now = datetime(2026, 8, 3, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def workspace_system(tmp_path):
    database = initialize_database(tmp_path / "identity.db")
    clock = Clock()
    notifications = MemoryNotificationProvider()
    authentication = AuthenticationService(
        database,
        policy=SessionPolicy(),
        notification_provider=notifications,
        public_origin="http://testserver",
        clock=clock,
        hasher=CHEAPEST_TEST_PASSWORD_HASHER,
    )
    workspaces = WorkspaceService(
        database,
        notification_provider=notifications,
        public_origin="http://testserver",
        clock=clock,
    )
    return authentication, workspaces, database, clock, notifications


def _signup(authentication, email):
    credentials = authentication.signup(email, "password", email.split("@")[0])
    return credentials, authentication.authenticate(credentials.session_token)


def _token(notifications, index=-1):
    return urlsplit(notifications.invitation_messages[index].invitation_url).path.rsplit("/", 1)[1]


def _add_member(authentication, workspaces, notifications, owner, workspace_id, email, role="member"):
    _, invited_user = _signup(authentication, email)
    invitation_id = workspaces.invite(owner, workspace_id, email, role).invitation_id
    accepted = workspaces.accept_invitation(invited_user, _token(notifications))
    return invited_user, accepted["membership_id"], invitation_id


@pytest.fixture
def workspace_shell_http(workspace_system):
    authentication, workspaces, database, _, notifications = workspace_system
    config = load_config(
        {
            "APP_ENV": "test",
            "IDENTITY_DB_PATH": str(database),
            "PUBLIC_ORIGIN": "http://testserver",
        }
    )
    AppCatalogService(database).sync(
        runtime_app_keys=frozenset(
            {"home", "notes", "calendar", "search", "publishing", "assets", "workspace"}
        )
    )
    authorization = AuthorizationService(database)
    app_policy = WorkspaceAppPolicyService(database, authorization)
    effective_apps = EffectiveWorkspaceApps(database, authorization)
    user_state = WorkspaceUserStateService(database, authorization)
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(
        create_workspace_shell_router(
            workspaces,
            authentication,
            config,
            app_policy,
            effective_apps,
            user_state,
        )
    )

    owner_credentials, owner = _signup(authentication, "settings-owner@example.com")
    workspace = workspaces.create_team(owner, "Settings Team")
    member_credentials, member = _signup(authentication, "settings-member@example.com")
    workspaces.invite(owner, workspace["id"], member.email, "member")
    workspaces.accept_invitation(member, _token(notifications))
    outsider_credentials, _ = _signup(authentication, "settings-outsider@example.com")

    with TestClient(app) as client:
        yield {
            "client": client,
            "cookie_name": config.session.cookie_name,
            "workspace_id": workspace["id"],
            "owner_token": owner_credentials.session_token,
            "member_token": member_credentials.session_token,
            "outsider_token": outsider_credentials.session_token,
        }


def _use_session(shell_http, token_key: str) -> TestClient:
    client = shell_http["client"]
    client.cookies.set(shell_http["cookie_name"], shell_http[token_key])
    return client


def test_workspace_shell_requires_projection_dependencies(workspace_system):
    authentication, workspaces, database, _, _ = workspace_system
    config = load_config(
        {
            "APP_ENV": "test",
            "IDENTITY_DB_PATH": str(database),
            "PUBLIC_ORIGIN": "http://testserver",
        }
    )

    with pytest.raises(TypeError):
        create_workspace_shell_router(workspaces, authentication, config)


def test_settings_root_returns_authentication_error_before_route_adapter(
    workspace_shell_http,
):
    response = workspace_shell_http["client"].get(
        f"/w/{workspace_shell_http['workspace_id']}/settings",
        follow_redirects=False,
    )

    assert response.status_code == 401
    assert response.json()["code"] == "authentication_required"


def test_settings_root_returns_non_disclosing_not_found_for_nonmember(
    workspace_shell_http,
):
    client = _use_session(workspace_shell_http, "outsider_token")
    response = client.get(
        f"/w/{workspace_shell_http['workspace_id']}/settings",
        follow_redirects=False,
    )

    assert response.status_code == 404
    assert response.json()["code"] == "workspace_not_found"


def test_settings_root_redirects_manager_directly_to_general(workspace_shell_http):
    client = _use_session(workspace_shell_http, "owner_token")
    workspace_id = workspace_shell_http["workspace_id"]

    response = client.get(f"/w/{workspace_id}/settings", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/w/{workspace_id}/settings/general"
    leaf = client.get(response.headers["location"], follow_redirects=False)
    assert leaf.status_code == 200


def test_settings_root_redirects_member_directly_to_members(workspace_shell_http):
    client = _use_session(workspace_shell_http, "member_token")
    workspace_id = workspace_shell_http["workspace_id"]

    response = client.get(f"/w/{workspace_id}/settings", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/w/{workspace_id}/settings/members"
    leaf = client.get(response.headers["location"], follow_redirects=False)
    assert leaf.status_code == 200


@pytest.mark.parametrize("section", ["general", "members", "apps", "roles", "permissions"])
def test_each_settings_page_uses_shared_shell_and_one_active_nav_item(
    workspace_shell_http,
    section,
):
    client = _use_session(workspace_shell_http, "owner_token")
    workspace_id = workspace_shell_http["workspace_id"]

    response = client.get(f"/w/{workspace_id}/settings/{section}")

    assert response.status_code == 200
    assert 'id="sidebar"' in response.text
    assert 'class="workspace-header"' in response.text
    assert 'id="workspace-account"' in response.text
    assert 'id="launcher"' in response.text
    assert 'data-current-app="workspace"' in response.text
    assert 'data-workspace-settings' in response.text
    assert 'aria-label="Workspace settings"' in response.text
    assert response.text.count('aria-current="page"') == 1
    assert f'href="/w/{workspace_id}/settings/{section}"' in response.text


def test_owner_settings_navigation_contains_every_authorized_section(
    workspace_shell_http,
):
    client = _use_session(workspace_shell_http, "owner_token")
    workspace_id = workspace_shell_http["workspace_id"]

    response = client.get(f"/w/{workspace_id}/settings/general")

    for section in ("general", "members", "apps", "roles", "permissions"):
        assert f'href="/w/{workspace_id}/settings/{section}"' in response.text


def test_member_settings_navigation_contains_only_members(workspace_shell_http):
    client = _use_session(workspace_shell_http, "member_token")
    workspace_id = workspace_shell_http["workspace_id"]

    response = client.get(f"/w/{workspace_id}/settings/members")

    assert response.status_code == 200
    assert f'href="/w/{workspace_id}/settings/members"' in response.text
    for section in ("general", "apps", "roles", "permissions"):
        assert f'href="/w/{workspace_id}/settings/{section}"' not in response.text
    assert "data-invitation-form" not in response.text


@pytest.mark.parametrize("section", ["general", "apps", "roles", "permissions"])
def test_member_cannot_open_privileged_settings_pages_directly(
    workspace_shell_http,
    section,
):
    client = _use_session(workspace_shell_http, "member_token")
    response = client.get(
        f"/w/{workspace_shell_http['workspace_id']}/settings/{section}"
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_team_creation_is_atomic_and_selector_rechecks_active_membership(workspace_system):
    authentication, workspaces, database, _, notifications = workspace_system
    _, owner = _signup(authentication, "owner@example.com")
    team = workspaces.create_team(owner, "Research", "Team")
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workspaces WHERE id=? AND type='TEAM'", (team["id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM roles WHERE workspace_id=?", (team["id"],)).fetchone()[0] == 4
        assert connection.execute("SELECT COUNT(*) FROM workspace_apps WHERE workspace_id=?", (team["id"],)).fetchone()[0] == 7
        assert connection.execute("SELECT COUNT(*) FROM workspace_members WHERE workspace_id=? AND status='ACTIVE'", (team["id"],)).fetchone()[0] == 1
    member, member_id, _ = _add_member(authentication, workspaces, notifications, owner, team["id"], "member@example.com")
    workspaces.select_workspace(member, team["id"])
    workspaces.remove_member(owner, team["id"], member_id)
    with pytest.raises(WorkspaceNotFound):
        workspaces.select_workspace(member, team["id"])
    with pytest.raises(ValueError, match="IANA timezone"):
        workspaces.create_team(owner, "Invalid timezone", timezone_name="Not/A_Zone")


def test_invitation_hash_one_use_expiry_duplicate_and_personal_rejection(workspace_system):
    authentication, workspaces, database, clock, notifications = workspace_system
    owner_credentials, owner = _signup(authentication, "owner@example.com")
    personal_id = owner_credentials.workspace_id
    with pytest.raises(WorkspaceConflict):
        workspaces.invite(owner, personal_id, "invitee@example.com", "member")
    team = workspaces.create_team(owner, "Team")
    _, invitee = _signup(authentication, "invitee@example.com")
    invitation_id = workspaces.invite(owner, team["id"], invitee.email, "member").invitation_id
    token = _token(notifications)
    with sqlite3.connect(database) as connection:
        stored = connection.execute("SELECT token_hash FROM workspace_invitations WHERE id=?", (invitation_id,)).fetchone()[0]
    assert stored == hashlib.sha256(token.encode()).hexdigest()
    assert token.encode() not in database.read_bytes()
    with pytest.raises(WorkspaceConflict):
        workspaces.invite(owner, team["id"], invitee.email, "viewer")
    workspaces.accept_invitation(invitee, token)
    with pytest.raises(WorkspaceConflict):
        workspaces.accept_invitation(invitee, token)

    _, late = _signup(authentication, "late@example.com")
    workspaces.invite(owner, team["id"], late.email, "member")
    late_token = _token(notifications)
    clock.now += timedelta(hours=25)
    with pytest.raises(WorkspaceConflict):
        workspaces.accept_invitation(late, late_token)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM workspace_invitations WHERE token_hash=?", (hashlib.sha256(late_token.encode()).hexdigest(),)).fetchone()[0] == "EXPIRED"


def test_invitation_accept_cancel_resend_race_has_one_winner(workspace_system):
    authentication, workspaces, database, _, notifications = workspace_system
    _, owner = _signup(authentication, "owner@example.com")
    team = workspaces.create_team(owner, "Team")
    _, invitee = _signup(authentication, "invitee@example.com")
    invitation_id = workspaces.invite(owner, team["id"], invitee.email, "member").invitation_id
    token = _token(notifications)
    barrier = threading.Barrier(3)

    def attempt(operation):
        barrier.wait()
        try:
            operation()
            return "success"
        except WorkspaceConflict:
            return "conflict"

    operations = (
        lambda: workspaces.accept_invitation(invitee, token),
        lambda: workspaces.cancel_invitation(owner, team["id"], invitation_id),
        lambda: workspaces.resend_invitation(owner, team["id"], invitation_id),
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(attempt, operations))
    assert outcomes.count("success") == 1
    with sqlite3.connect(database) as connection:
        old = connection.execute("SELECT status FROM workspace_invitations WHERE id=?", (invitation_id,)).fetchone()[0]
        pending = connection.execute("SELECT COUNT(*) FROM workspace_invitations WHERE workspace_id=? AND email_normalized=? AND status='PENDING'", (team["id"], invitee.email)).fetchone()[0]
    assert old in {"ACCEPTED", "CANCELED"}
    assert pending <= 1


def test_invitation_delivery_failure_preserves_recoverable_id_and_audit(workspace_system):
    authentication, workspaces, database, _, _ = workspace_system
    _, owner = _signup(authentication, "owner@example.com")
    team = workspaces.create_team(owner, "Team")

    class FailingProvider:
        def send_workspace_invitation(self, _message):
            raise RuntimeError("provider unavailable")

    workspaces.notification_provider = FailingProvider()
    result = workspaces.invite(owner, team["id"], "invitee@example.com", "member")
    invitation_id = result.invitation_id
    with sqlite3.connect(database) as connection:
        invitation = connection.execute("SELECT status FROM workspace_invitations WHERE id=?", (invitation_id,)).fetchone()
        audit = connection.execute("SELECT COUNT(*) FROM audit_logs WHERE action='invitation.delivery_failed' AND target_id=?", (invitation_id,)).fetchone()[0]
    assert invitation == ("PENDING",)
    assert audit == 1
    assert result.delivery_status == "FAILED"


def test_rejoin_creates_new_membership_without_old_overrides(workspace_system):
    authentication, workspaces, database, _, notifications = workspace_system
    _, owner = _signup(authentication, "owner@example.com")
    team = workspaces.create_team(owner, "Team")
    member, old_id, _ = _add_member(authentication, workspaces, notifications, owner, team["id"], "member@example.com")
    with database_connection(database) as tx:
        permission = tx.execute("SELECT id FROM permissions WHERE code='notes.delete_all'").fetchone()["id"]
        tx.execute("INSERT INTO member_permission_overrides VALUES (?, ?, ?, 'ALLOW', ?, '2026-08-03T00:00:00Z', '2026-08-03T00:00:00Z')", (team["id"], old_id, permission, owner.id))
    workspaces.remove_member(owner, team["id"], old_id)
    workspaces.invite(owner, team["id"], member.email, "member")
    accepted = workspaces.accept_invitation(member, _token(notifications))
    assert accepted["membership_id"] != old_id
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM member_permission_overrides WHERE membership_id=?", (accepted["membership_id"],)).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM member_permission_overrides WHERE membership_id=?", (old_id,)).fetchone()[0] == 1


def test_admin_cannot_mutate_owner_or_owner_only_lifecycle(workspace_system):
    authentication, workspaces, _, _, notifications = workspace_system
    _, owner = _signup(authentication, "owner@example.com")
    team = workspaces.create_team(owner, "Team")
    admin, admin_id, _ = _add_member(authentication, workspaces, notifications, owner, team["id"], "admin@example.com", "admin")
    owner_id = team["membership_id"]
    with pytest.raises(WorkspaceForbidden):
        workspaces.change_member_role(admin, team["id"], owner_id, "member")
    with pytest.raises(WorkspaceForbidden):
        workspaces.remove_member(admin, team["id"], owner_id)
    with pytest.raises(WorkspaceForbidden):
        workspaces.delete_workspace(admin, team["id"])
    with pytest.raises(WorkspaceForbidden):
        workspaces.transfer_ownership(admin, team["id"], admin_id)


def test_concurrent_owner_demotions_preserve_at_least_one_owner(workspace_system):
    authentication, workspaces, database, _, notifications = workspace_system
    _, first = _signup(authentication, "first@example.com")
    team = workspaces.create_team(first, "Team")
    second, second_id, _ = _add_member(authentication, workspaces, notifications, first, team["id"], "second@example.com", "owner")
    first_id = team["membership_id"]
    barrier = threading.Barrier(2)

    def demote(actor, target):
        barrier.wait()
        try:
            workspaces.change_member_role(actor, team["id"], target, "admin")
            return "success"
        except (WorkspaceConflict, WorkspaceForbidden, WorkspaceNotFound):
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda args: demote(*args), ((first, second_id), (second, first_id))))
    assert outcomes.count("success") == 1
    with sqlite3.connect(database) as connection:
        owners = connection.execute("SELECT COUNT(*) FROM workspace_members AS member JOIN roles AS role ON role.id=member.role_id WHERE member.workspace_id=? AND member.status='ACTIVE' AND role.system_role='OWNER'", (team["id"],)).fetchone()[0]
    assert owners == 1


def test_concurrent_owner_demote_remove_transfer_mix_preserves_owner(workspace_system):
    authentication, workspaces, database, _, notifications = workspace_system
    _, first = _signup(authentication, "first@example.com")
    team = workspaces.create_team(first, "Team")
    second, second_id, _ = _add_member(authentication, workspaces, notifications, first, team["id"], "second@example.com", "owner")
    third, third_id, _ = _add_member(authentication, workspaces, notifications, first, team["id"], "third@example.com", "owner")
    first_id = team["membership_id"]
    barrier = threading.Barrier(3)

    def attempt(operation):
        barrier.wait()
        try:
            operation()
            return "success"
        except (WorkspaceConflict, WorkspaceForbidden, WorkspaceNotFound):
            return "rejected"

    operations = (
        lambda: workspaces.change_member_role(first, team["id"], second_id, "admin"),
        lambda: workspaces.remove_member(second, team["id"], first_id),
        lambda: workspaces.transfer_ownership(third, team["id"], second_id),
    )
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(attempt, operations))
    with sqlite3.connect(database) as connection:
        owners = connection.execute("SELECT COUNT(*) FROM workspace_members AS member JOIN roles AS role ON role.id=member.role_id WHERE member.workspace_id=? AND member.status='ACTIVE' AND role.system_role='OWNER'", (team["id"],)).fetchone()[0]
    assert owners >= 1


def test_soft_delete_cancels_invites_and_hides_workspace(workspace_system):
    authentication, workspaces, database, _, notifications = workspace_system
    _, owner = _signup(authentication, "owner@example.com")
    team = workspaces.create_team(owner, "Team")
    workspaces.invite(owner, team["id"], "invitee@example.com", "member")
    workspaces.delete_workspace(owner, team["id"])
    with pytest.raises(WorkspaceNotFound):
        workspaces.get_workspace(owner, team["id"])
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT status FROM workspaces WHERE id=?", (team["id"],)).fetchone()[0] == "DELETED"
        assert connection.execute("SELECT status FROM workspace_invitations WHERE workspace_id=?", (team["id"],)).fetchone()[0] == "CANCELED"


def test_workspace_http_contract_is_versioned_csrf_protected_and_secret_safe(workspace_system, tmp_path):
    authentication, workspaces, database, _, notifications = workspace_system
    config = load_config({"APP_ENV": "test", "IDENTITY_DB_PATH": str(database), "PUBLIC_ORIGIN": "http://testserver"})
    AppCatalogService(database).sync(runtime_app_keys=frozenset({"home", "notes", "calendar", "search", "publishing", "assets", "workspace"}))
    authorization = AuthorizationService(database)
    app_policy = WorkspaceAppPolicyService(database, authorization)
    effective = EffectiveWorkspaceApps(database, authorization)
    notes_database = initialize_notes_database(tmp_path / "workspace-http-notes.db")
    calendar_database = tmp_path / "workspace-http-calendar.db"
    initialize_calendar_database(calendar_database)
    user_state = WorkspaceUserStateService(
        database, authorization,
        WorkspaceUserStateResourceResolver(notes_database, calendar_database, authorization),
    )
    notes_service = NotesService(notes_database, authorization=authorization)
    app = FastAPI(); install_exception_handlers(app)
    app.include_router(create_auth_router(authentication, config))
    app.include_router(create_workspace_router(workspaces, authentication, config, app_policy))
    app.include_router(create_user_state_router(user_state, workspaces, authentication, config))
    app.include_router(create_workspace_shell_router(workspaces, authentication, config, app_policy, effective, user_state))
    origin = {"Origin": "http://testserver"}
    with TestClient(app) as client:
        signup = client.post("/api/v1/auth/signup", headers=origin, json={"email": "owner@example.com", "password": "password"})
        csrf = signup.json()["csrf_token"]
        assert client.post("/api/v1/workspaces", headers=origin, json={"name": "Team"}).status_code == 403
        team = client.post("/api/v1/workspaces", headers={**origin, "X-CSRF-Token": csrf}, json={"name": "Team"})
        assert team.status_code == 201
        team_id = team.json()["id"]
        owner = authentication.authenticate(client.cookies.get(config.session.cookie_name))
        note = notes_service.create_note(
            authorization.resolve_context(owner.id, team_id),
            NoteCreate(title="Example", content="", note_type="general", is_pinned=False, links=[]),
        )
        shell = client.get(f"/w/{team.json()['id']}/settings/general")
        assert shell.status_code == 200
        assert 'aria-label="Workspace settings"' in shell.text
        assert 'data-workspace-selector' in shell.text
        assert 'data-workspace-settings' in shell.text
        assert 'id="sidebar"' in shell.text
        assert 'data-current-app="workspace"' in shell.text
        assert f'href="/w/{team.json()["id"]}/home"' in shell.text
        assert client.get("/api/v1/workspaces").status_code == 200
        home = client.get(f"/w/{team.json()['id']}/home")
        assert home.status_code == 200 and 'data-app-key="notes"' in home.text
        assert 'data-app-key="search"' in home.text
        assert 'data-workspace-home' in home.text
        assert 'id="sidebar"' in home.text
        assert "바로 시작하기" in home.text
        assert 'data-dashboard' in home.text
        assert client.get(f"/w/{team.json()['id']}/settings/permissions").status_code == 200
        favorite = client.put(
            f"/api/v1/workspaces/{team_id}/favorites/note/{note['id']}",
            headers={**origin, "X-CSRF-Token": csrf},
            json={"app_key": "notes"},
        )
        assert favorite.status_code == 204
        assert client.get(f"/api/v1/workspaces/{team.json()['id']}/favorites").json()[0]["label"] == "Example"
        assert client.post(
            f"/api/v1/workspaces/{team_id}/recent-views",
            headers={**origin, "X-CSRF-Token": csrf},
            json={"resource_type": "note", "resource_id": str(note["id"]), "app_key": "notes"},
        ).status_code == 204
        assert client.get(f"/api/v1/workspaces/{team_id}/recent-views").json()[0]["resource_id"] == str(note["id"])
        assert client.get(f"/api/v1/workspaces/{team.json()['id']}/activity").status_code == 200
        notes_adapter = client.get(f"/w/{team.json()['id']}/notes", follow_redirects=False)
        assert notes_adapter.status_code == 307 and notes_adapter.headers["location"] == "/apps/notes/"
        publishing_adapter = client.get(f"/w/{team.json()['id']}/publications", follow_redirects=False)
        assert publishing_adapter.status_code == 307 and publishing_adapter.headers["location"] == "/publications/"
        assets_disabled = client.patch(f"/api/v1/workspaces/{team.json()['id']}/apps/assets", headers={**origin, "X-CSRF-Token": csrf}, json={"enabled": False})
        assert assets_disabled.status_code == 204
        assert client.get(f"/api/v1/workspaces/{team.json()['id']}/apps/assets/access").status_code == 404
        assets_enabled = client.patch(f"/api/v1/workspaces/{team.json()['id']}/apps/assets", headers={**origin, "X-CSRF-Token": csrf}, json={"enabled": True})
        assert assets_enabled.status_code == 204
        disabled = client.patch(f"/api/v1/workspaces/{team.json()['id']}/apps/notes", headers={**origin, "X-CSRF-Token": csrf}, json={"enabled": False})
        assert disabled.status_code == 204
        assert client.get(f"/api/v1/workspaces/{team.json()['id']}/apps/notes/access").status_code == 404
        assert 'data-app-key="notes"' not in client.get(f"/w/{team.json()['id']}/home").text
        invitation = client.post(f"/api/v1/workspaces/{team.json()['id']}/invitations", headers={**origin, "X-CSRF-Token": csrf}, json={"email": "invitee@example.com", "role_key": "member"})
        assert invitation.status_code == 201
        assert "token" not in invitation.text
        member_credentials, member = _signup(authentication, "invitee@example.com")
        accepted = workspaces.accept_invitation(member, _token(notifications))
        denied = client.put(
            f"/api/v1/workspaces/{team.json()['id']}/members/{accepted['membership_id']}/permissions/notes.create",
            headers={**origin, "X-CSRF-Token": csrf}, json={"effect": "DENY"},
        )
        assert denied.status_code == 204
        client.cookies.set(config.session.cookie_name, member_credentials.session_token)
        member_home = client.get(f"/w/{team.json()['id']}/home")
        assert member_home.status_code == 200
        assert 'aria-label="Workspace settings"' not in member_home.text
        assert client.get(f"/w/{team.json()['id']}/settings/general").status_code == 403
        assert client.get(f"/w/{team.json()['id']}/settings/apps").status_code == 403
        assert client.get(f"/w/{team.json()['id']}/settings/permissions").status_code == 403
        assert client.get(f"/api/v1/workspaces/{team.json()['id']}/permissions").status_code == 403
        member_page = client.get(f"/w/{team.json()['id']}/settings/members")
        assert member_page.status_code == 200
        assert "data-invitation-form" not in member_page.text
        paths = app.openapi()["paths"]
        assert all(path.startswith("/api/v1/") for path in paths)
        assert "post" in paths["/api/v1/workspaces/{workspace_id}/recent-views"]
        schemas = app.openapi()["components"]["schemas"]
        assert "AppPolicyResponse" in schemas and "RolePolicyResponse" in schemas
        apps_schema = paths["/api/v1/workspaces/{workspace_id}/apps"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        roles_schema = paths["/api/v1/workspaces/{workspace_id}/roles"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert apps_schema["items"]["$ref"].endswith("/AppPolicyResponse")
        assert roles_schema["items"]["$ref"].endswith("/RolePolicyResponse")
    raw = _token(notifications)
    line = f'GET /invitations/{raw} /api/v1/invitations/{raw}/accept?x=1'
    redacted = redact_auth_tokens(line)
    assert raw not in redacted and redacted.count("[REDACTED]") == 2
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s", (line,), None)
    AuthTokenRedactionFilter().filter(record)
    assert raw not in record.args[0]
