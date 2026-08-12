from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from access.dependencies import workspace_dependency
from access.service import AuthorizationService
from apps.notes.app.database import initialize_database as initialize_notes_database
from apps.notes.app.main import create_notes_router
from apps.notes.app.service import NotesService
from fastapi import FastAPI, Request
from identity.auth import (
    AuthenticationForbidden,
    AuthenticationRequired,
    AuthenticationService,
)
from identity.database import initialize_database
from identity.notifications import MemoryNotificationProvider
from identity.passwords import CHEAPEST_TEST_PASSWORD_HASHER
from identity.router import _auth_error, _require_same_origin
from identity.workspace_router import create_workspace_router
from identity.workspaces import WorkspaceService
from shared.app_registry import AppRegistry
from shared.config import load_config
from shared.errors import install_exception_handlers
from workspace_platform.app_catalog import AppCatalogService
from workspace_platform.app_policy import WorkspaceAppPolicyService


ORIGIN = {"Origin": "http://testserver"}
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _invitation_token(notifications: MemoryNotificationProvider) -> str:
    return urlsplit(notifications.invitation_messages[-1].invitation_url).path.rsplit(
        "/", 1
    )[-1]


@pytest.fixture
async def permission_override_http(tmp_path: Path):
    identity_path = initialize_database(tmp_path / "identity.db")
    notifications = MemoryNotificationProvider()
    authentication = AuthenticationService(
        identity_path,
        notification_provider=notifications,
        public_origin="http://testserver",
        hasher=CHEAPEST_TEST_PASSWORD_HASHER,
    )
    workspaces = WorkspaceService(
        identity_path,
        notification_provider=notifications,
        public_origin="http://testserver",
        clock=authentication.clock,
    )

    owner_credentials = authentication.signup(
        "override-owner@example.com", "password", "Override Owner"
    )
    owner = authentication.authenticate(owner_credentials.session_token)
    workspace = workspaces.create_team(owner, "Permission Override Team")

    actors = {"owner": owner_credentials}
    memberships = {"owner": workspace["membership_id"]}
    for role in ("admin", "member", "author"):
        credentials = authentication.signup(
            f"override-{role}@example.com", "password", role.title()
        )
        authenticated = authentication.authenticate(credentials.session_token)
        workspaces.invite(owner, workspace["id"], authenticated.email, "member" if role == "author" else role)
        accepted = workspaces.accept_invitation(
            authenticated, _invitation_token(notifications)
        )
        actors[role] = credentials
        memberships[role] = accepted["membership_id"]

    outsider_credentials = authentication.signup(
        "override-outsider@example.com", "password", "Outsider"
    )
    with sqlite3.connect(identity_path) as connection:
        outsider_membership_id = connection.execute(
            "SELECT id FROM workspace_members WHERE workspace_id=? AND user_id=?",
            (outsider_credentials.workspace_id, outsider_credentials.user_id),
        ).fetchone()[0]

    config = load_config(
        {
            "APP_ENV": "test",
            "IDENTITY_DB_PATH": str(identity_path),
            "PUBLIC_ORIGIN": "http://testserver",
        }
    )
    cookie_name = config.session.cookie_name
    AppCatalogService(identity_path).sync(
        runtime_app_keys=frozenset(
            {"home", "notes", "calendar", "search", "publishing", "assets", "workspace"}
        )
    )
    authorization = AuthorizationService(identity_path)
    app_policy = WorkspaceAppPolicyService(identity_path, authorization)
    notes_path = initialize_notes_database(tmp_path / "notes.db")
    notes = NotesService(notes_path, authorization=authorization)
    require_workspace = workspace_dependency(authentication, authorization, cookie_name)

    def csrf(request: Request) -> None:
        try:
            authenticated = authentication.authenticate(
                request.cookies.get(cookie_name)
            )
        except AuthenticationRequired as error:
            raise _auth_error(error) from error
        _require_same_origin(request, config.public_origin)
        try:
            authentication.validate_csrf(
                authenticated.session_id, request.headers.get("x-csrf-token")
            )
        except AuthenticationForbidden as error:
            raise _auth_error(error) from error

    application = FastAPI()
    install_exception_handlers(application)
    application.include_router(
        create_workspace_router(workspaces, authentication, config, app_policy)
    )
    application.include_router(
        create_notes_router(
            service=notes,
            app_registry=AppRegistry.load_default(),
            require_workspace=require_workspace,
            csrf=csrf,
            csrf_cookie_name=f"{cookie_name}_csrf",
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://testserver",
    ) as client:
        yield {
            "client": client,
            "cookie_name": cookie_name,
            "identity_path": identity_path,
            "workspace_id": workspace["id"],
            "actors": actors,
            "memberships": memberships,
            "outsider_membership_id": outsider_membership_id,
        }


def _use_actor(system, actor: str) -> AsyncClient:
    credentials = system["actors"][actor]
    client = system["client"]
    client.cookies.set(system["cookie_name"], credentials.session_token)
    client.cookies.set(f"{system['cookie_name']}_csrf", credentials.csrf_token)
    return client


def _csrf(system, actor: str) -> dict[str, str]:
    return {
        **ORIGIN,
        "X-CSRF-Token": system["actors"][actor].csrf_token,
    }


def _override_url(system, member: str, permission: str) -> str:
    return (
        f"/api/v1/workspaces/{system['workspace_id']}/members/"
        f"{system['memberships'][member]}/permissions/{permission}"
    )


async def _create_note(system, actor: str, title: str) -> dict:
    response = await _use_actor(system, actor).post(
        f"/api/v1/workspaces/{system['workspace_id']}/notes",
        headers=_csrf(system, actor),
        json={"title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_admin_deny_delete_all_blocks_the_next_domain_delete_request(
    permission_override_http,
):
    system = permission_override_http
    note = await _create_note(system, "author", "Must survive an admin deny")

    denied = await _use_actor(system, "owner").put(
        _override_url(system, "admin", "notes.delete_all"),
        headers=_csrf(system, "owner"),
        json={"effect": "DENY"},
    )
    assert denied.status_code == 204, denied.text

    response = await _use_actor(system, "admin").delete(
        f"/api/v1/workspaces/{system['workspace_id']}/notes/{note['id']}",
        headers=_csrf(system, "admin"),
    )

    assert response.status_code == 403


async def test_member_allow_update_all_enables_updating_another_members_note(
    permission_override_http,
):
    system = permission_override_http
    note = await _create_note(system, "author", "Authored by another member")
    allowed = await _use_actor(system, "owner").put(
        _override_url(system, "member", "notes.update_all"),
        headers=_csrf(system, "owner"),
        json={"effect": "ALLOW"},
    )
    assert allowed.status_code == 204, allowed.text

    response = await _use_actor(system, "member").put(
        f"/api/v1/workspaces/{system['workspace_id']}/notes/{note['id']}",
        headers=_csrf(system, "member"),
        json={"title": "Updated through explicit allow"},
    )

    assert response.status_code == 200
    assert response.json()["title"] == "Updated through explicit allow"


async def test_clearing_allow_blocks_the_next_request_in_the_same_member_session(
    permission_override_http,
):
    system = permission_override_http
    note = await _create_note(system, "author", "Override invalidation target")
    await _use_actor(system, "owner").put(
        _override_url(system, "member", "notes.update_all"),
        headers=_csrf(system, "owner"),
        json={"effect": "ALLOW"},
    )
    first = await _use_actor(system, "member").put(
        f"/api/v1/workspaces/{system['workspace_id']}/notes/{note['id']}",
        headers=_csrf(system, "member"),
        json={"title": "First request is allowed"},
    )
    assert first.status_code == 200, first.text

    cleared = await _use_actor(system, "owner").delete(
        _override_url(system, "member", "notes.update_all"),
        headers=_csrf(system, "owner"),
    )
    assert cleared.status_code == 204, cleared.text

    response = await _use_actor(system, "member").put(
        f"/api/v1/workspaces/{system['workspace_id']}/notes/{note['id']}",
        headers=_csrf(system, "member"),
        json={"title": "Second request must be denied"},
    )

    assert response.status_code == 403


async def test_ordinary_member_cannot_self_grant_permission_management(
    permission_override_http,
):
    system = permission_override_http

    response = await _use_actor(system, "member").put(
        _override_url(system, "member", "workspace.permissions.manage"),
        headers=_csrf(system, "member"),
        json={"effect": "ALLOW"},
    )

    assert response.status_code == 403


async def test_cross_workspace_target_membership_is_non_disclosing_not_found(
    permission_override_http,
):
    system = permission_override_http
    url = (
        f"/api/v1/workspaces/{system['workspace_id']}/members/"
        f"{system['outsider_membership_id']}/permissions/notes.update_all"
    )

    response = await _use_actor(system, "owner").put(
        url,
        headers=_csrf(system, "owner"),
        json={"effect": "ALLOW"},
    )

    assert response.status_code == 404
    assert "outsider" not in response.text.lower()


async def test_allow_put_persists_the_override(permission_override_http):
    system = permission_override_http

    response = await _use_actor(system, "owner").put(
        _override_url(system, "member", "notes.update_all"),
        headers=_csrf(system, "owner"),
        json={"effect": "ALLOW"},
    )

    assert response.status_code == 204, response.text
    with sqlite3.connect(system["identity_path"]) as connection:
        stored = connection.execute(
            """SELECT override.effect
            FROM member_permission_overrides AS override
            JOIN permissions AS permission ON permission.id=override.permission_id
            WHERE override.workspace_id=? AND override.membership_id=?
              AND permission.code='notes.update_all'""",
            (system["workspace_id"], system["memberships"]["member"]),
        ).fetchone()
    assert stored == ("ALLOW",)


async def test_delete_persists_override_removal(permission_override_http):
    system = permission_override_http
    url = _override_url(system, "member", "notes.update_all")
    await _use_actor(system, "owner").put(
        url,
        headers=_csrf(system, "owner"),
        json={"effect": "ALLOW"},
    )

    response = await _use_actor(system, "owner").delete(
        url,
        headers=_csrf(system, "owner"),
    )

    assert response.status_code == 204, response.text
    with sqlite3.connect(system["identity_path"]) as connection:
        remaining = connection.execute(
            """SELECT COUNT(*)
            FROM member_permission_overrides AS override
            JOIN permissions AS permission ON permission.id=override.permission_id
            WHERE override.workspace_id=? AND override.membership_id=?
              AND permission.code='notes.update_all'""",
            (system["workspace_id"], system["memberships"]["member"]),
        ).fetchone()[0]
    assert remaining == 0


async def test_successful_override_mutations_append_change_and_clear_audit_rows(
    permission_override_http,
):
    system = permission_override_http
    url = _override_url(system, "member", "notes.update_all")
    await _use_actor(system, "owner").put(
        url,
        headers=_csrf(system, "owner"),
        json={"effect": "ALLOW"},
    )
    await _use_actor(system, "owner").delete(
        url,
        headers=_csrf(system, "owner"),
    )

    with sqlite3.connect(system["identity_path"]) as connection:
        actions = connection.execute(
            """SELECT action FROM audit_logs
            WHERE workspace_id=? AND target_type='membership' AND target_id=?
              AND action LIKE 'workspace.permission_override_%'
            ORDER BY rowid""",
            (system["workspace_id"], system["memberships"]["member"]),
        ).fetchall()

    assert actions == [
        ("workspace.permission_override_changed",),
        ("workspace.permission_override_cleared",),
    ]


async def test_failed_audit_insert_rolls_back_the_override_mutation(
    permission_override_http,
):
    system = permission_override_http
    with sqlite3.connect(system["identity_path"]) as connection:
        connection.execute(
            """CREATE TRIGGER fail_permission_override_audit
            BEFORE INSERT ON audit_logs
            WHEN NEW.action='workspace.permission_override_changed'
            BEGIN
                SELECT RAISE(ABORT, 'forced audit failure');
            END"""
        )

    response = await _use_actor(system, "owner").put(
        _override_url(system, "member", "notes.update_all"),
        headers=_csrf(system, "owner"),
        json={"effect": "ALLOW"},
    )

    assert response.status_code == 500
    with sqlite3.connect(system["identity_path"]) as connection:
        override_count = connection.execute(
            """SELECT COUNT(*)
            FROM member_permission_overrides AS override
            JOIN permissions AS permission ON permission.id=override.permission_id
            WHERE override.workspace_id=? AND override.membership_id=?
              AND permission.code='notes.update_all'""",
            (system["workspace_id"], system["memberships"]["member"]),
        ).fetchone()[0]
        audit_count = connection.execute(
            """SELECT COUNT(*) FROM audit_logs
            WHERE workspace_id=? AND target_id=?
              AND action='workspace.permission_override_changed'""",
            (system["workspace_id"], system["memberships"]["member"]),
        ).fetchone()[0]
    assert override_count == 0
    assert audit_count == 0
