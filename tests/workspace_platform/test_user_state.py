from __future__ import annotations

import sqlite3

import pytest
from pydantic import ValidationError

from access.service import AuthorizationService
from apps.notes.app.database import initialize_database as initialize_notes
from apps.notes.app.schemas import NoteCreate, NoteUpdate
from apps.notes.app.service import NotesService
from apps.calendar.app.main import initialize_workspace_database as initialize_calendar
from apps.calendar.app.service import CalendarService
from identity.auth import AuthenticationService
from identity.database import initialize_database
from identity.notifications import MemoryNotificationProvider
from identity.passwords import CHEAPEST_TEST_PASSWORD_HASHER
from identity.primitives import new_uuid
from shared.activity import SqliteActivitySink
from shared.internal_links import ProviderLink
from workspace_platform.app_catalog import AppCatalogService
from workspace_platform.app_policy import WorkspaceAppPolicyService
from workspace_platform.user_state import UserStateNotFound, WorkspaceUserStateService
from workspace_platform.user_state_resources import ResourceResolution, WorkspaceUserStateResourceResolver
from workspace_platform.user_state_router import ResourceStateRequest


RUNTIME_KEYS = frozenset({"home", "notes", "calendar", "search", "publishing", "assets", "workspace"})


def _system(tmp_path):
    identity = initialize_database(tmp_path / "identity.db")
    notes = initialize_notes(tmp_path / "notes.db")
    calendar = tmp_path / "calendar.db"
    initialize_calendar(calendar)
    authentication = AuthenticationService(
        identity, notification_provider=MemoryNotificationProvider(),
        public_origin="http://testserver", hasher=CHEAPEST_TEST_PASSWORD_HASHER,
    )
    credentials = authentication.signup("owner@example.com", "password", "Owner")
    user = authentication.authenticate(credentials.session_token)
    AppCatalogService(identity).sync(runtime_app_keys=RUNTIME_KEYS)
    authorization = AuthorizationService(identity)
    context = authorization.resolve_context(user.id, credentials.workspace_id)
    resolver = WorkspaceUserStateResourceResolver(notes, calendar, authorization)
    state = WorkspaceUserStateService(identity, authorization, resolver)
    service = NotesService(notes, authorization=authorization, activity_sink=SqliteActivitySink(identity))
    return identity, notes, calendar, user, context, authorization, state, service


def _note(service, context, title="Canonical title"):
    return service.create_note(context, NoteCreate(
        title=title, content="body", note_type="general", is_pinned=False, links=[]
    ))


def test_server_resolves_canonical_resource_and_activity_is_not_audit(tmp_path):
    identity, _, _, _, context, _, state, notes = _system(tmp_path)
    note = _note(notes, context)
    state.set_favorite(context, "note", str(note["id"]), app_key="notes")
    state.record_recent(context, "note", str(note["id"]), app_key="notes")

    favorite = state.list_favorites(context)[0]
    assert favorite["label"] == "Canonical title"
    assert favorite["href"] == f"/w/{context.workspace_id}/notes?note_id={note['id']}"
    assert state.list_recent(context)[0]["resource_id"] == str(note["id"])
    assert state.list_activity(context)[0]["action"] == "notes.created"
    with sqlite3.connect(identity) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action LIKE 'notes.%' OR action LIKE 'workspace.favorite_%'"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM activity_logs").fetchone()[0] == 1


def test_missing_resources_are_cleaned_but_disabled_apps_are_only_hidden(tmp_path):
    identity, _, _, user, context, authorization, state, notes = _system(tmp_path)
    note = _note(notes, context)
    state.set_favorite(context, "note", str(note["id"]), app_key="notes")
    WorkspaceAppPolicyService(identity, authorization).update_app(
        user.id, context.workspace_id, "notes", enabled=False,
        display_order=None, settings=None,
    )
    refreshed = authorization.resolve_context(user.id, context.workspace_id)
    assert state.list_favorites(refreshed) == []
    with sqlite3.connect(identity) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workspace_favorites").fetchone()[0] == 1

    WorkspaceAppPolicyService(identity, authorization).update_app(
        user.id, context.workspace_id, "notes", enabled=True,
        display_order=None, settings=None,
    )
    restored = authorization.resolve_context(user.id, context.workspace_id)
    notes.delete_note(restored, int(note["id"]))
    assert state.list_favorites(restored) == []
    with sqlite3.connect(identity) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workspace_favorites").fetchone()[0] == 0


def test_recent_is_debounced_and_cross_workspace_resource_is_rejected(tmp_path):
    identity, _, _, _, context, _, state, notes = _system(tmp_path)
    note = _note(notes, context)
    state.record_recent(context, "note", str(note["id"]), app_key="notes")
    first = state.list_recent(context)[0]["viewed_at"]
    state.record_recent(context, "note", str(note["id"]), app_key="notes")
    assert state.list_recent(context)[0]["viewed_at"] == first
    with pytest.raises(UserStateNotFound):
        state.set_favorite(context, "note", "999999", app_key="notes")
    with pytest.raises(UserStateNotFound):
        state.set_favorite(context, "note", str(note["id"]), app_key="calendar")


def test_activity_tie_order_is_deterministic_and_workspace_scoped(tmp_path):
    identity, _, _, _, context, _, state, _ = _system(tmp_path)
    with sqlite3.connect(identity) as connection:
        app_key = "notes"
        stamp = "2026-08-11T00:00:00Z"
        for resource_id in ("1", "2"):
            connection.execute(
                "INSERT INTO activity_logs(workspace_id,actor_user_id,app_key,action,resource_type,resource_id,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (context.workspace_id, context.user_id, app_key, "notes.updated", "note", resource_id,
                 "{}", stamp),
            )
    assert [item["target_id"] for item in state.list_activity(context)] == ["2", "1"]


def test_provider_failure_hides_state_without_deleting_it(tmp_path, monkeypatch):
    identity, _, _, _, context, _, state, notes = _system(tmp_path)
    note = _note(notes, context)
    state.set_favorite(context, "note", str(note["id"]), app_key="notes")
    monkeypatch.setattr(
        state.resources,
        "resolve_many",
        lambda ctx, grouped: {
            (resource_type, resource_id): ResourceResolution("unavailable", "notes")
            for resource_type, resource_ids in grouped.items()
            for resource_id in resource_ids
        },
    )
    assert state.list_favorites(context) == []
    with sqlite3.connect(identity) as connection:
        assert connection.execute("SELECT COUNT(*) FROM workspace_favorites").fetchone()[0] == 1


def test_recent_retains_only_latest_100_per_user_and_workspace(tmp_path):
    identity, _, _, _, context, authorization, _, _ = _system(tmp_path)

    class Resources:
        def resolve_one(self, ctx, resource_type, resource_id):
            return ResourceResolution(
                "visible", "notes", ProviderLink(
                    target=resource_id,
                    href=f"/w/{ctx.workspace_id}/notes?note_id={resource_id}",
                    label=f"Note {resource_id}",
                ),
            )

        def resolve_many(self, ctx, grouped):
            return {
                (resource_type, resource_id): self.resolve_one(ctx, resource_type, resource_id)
                for resource_type, resource_ids in grouped.items()
                for resource_id in resource_ids
            }

    state = WorkspaceUserStateService(identity, authorization, Resources())
    for index in range(105):
        state.record_recent(context, "note", str(index), app_key="notes")
    with sqlite3.connect(identity) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workspace_recent_views WHERE workspace_id=? AND user_id=?",
            (context.workspace_id, context.user_id),
        ).fetchone()[0] == 100
    recent = state.list_recent(context, limit=100)
    assert len(recent) == 100
    assert recent[0]["resource_id"] == "104"
    assert recent[-1]["resource_id"] == "5"


def test_user_state_write_contract_accepts_only_app_key():
    assert ResourceStateRequest(app_key="notes").model_dump() == {"app_key": "notes"}
    with pytest.raises(ValidationError):
        ResourceStateRequest(
            app_key="notes", label="client controlled", href="https://evil.test"
        )


def test_user_and_workspace_isolation_permission_revoke_restore_and_recent_cleanup(tmp_path):
    identity, _, _, owner, owner_context, authorization, owner_state, notes = _system(tmp_path)
    owner_note = _note(notes, owner_context, "Owner note")
    authentication = AuthenticationService(
        identity, notification_provider=MemoryNotificationProvider(),
        public_origin="http://testserver", hasher=CHEAPEST_TEST_PASSWORD_HASHER,
    )
    credentials = authentication.signup("member@example.com", "password", "Member")
    member = authentication.authenticate(credentials.session_token)
    other_context = authorization.resolve_context(member.id, credentials.workspace_id)
    other_note = _note(notes, other_context, "Other workspace note")

    membership_id = new_uuid()
    stamp = "2026-08-11T00:00:00Z"
    with sqlite3.connect(identity) as connection:
        role_id = connection.execute(
            "SELECT id FROM roles WHERE workspace_id=? AND system_role='MEMBER'",
            (owner_context.workspace_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
            (membership_id, owner_context.workspace_id, member.id, role_id, stamp, stamp, stamp),
        )
    owner_context = authorization.resolve_context(owner.id, owner_context.workspace_id)
    member_context = authorization.resolve_context(member.id, owner_context.workspace_id)
    member_state = WorkspaceUserStateService(identity, authorization, owner_state.resources)
    member_state.set_favorite(member_context, "note", str(owner_note["id"]), app_key="notes")
    assert owner_state.list_favorites(owner_context) == []
    assert [item["resource_id"] for item in member_state.list_favorites(member_context)] == [str(owner_note["id"])]

    policy = WorkspaceAppPolicyService(identity, authorization)
    policy.set_permission_override(
        owner.id, owner_context.workspace_id, membership_id, "notes.read_all", "DENY"
    )
    denied_context = authorization.resolve_context(member.id, owner_context.workspace_id)
    assert member_state.list_favorites(denied_context) == []
    with sqlite3.connect(identity) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workspace_favorites WHERE workspace_id=? AND user_id=?",
            (owner_context.workspace_id, member.id),
        ).fetchone()[0] == 1
    policy.clear_permission_override(
        owner.id, owner_context.workspace_id, membership_id, "notes.read_all"
    )
    restored_context = authorization.resolve_context(member.id, owner_context.workspace_id)
    assert len(member_state.list_favorites(restored_context)) == 1
    owner_context = authorization.resolve_context(owner.id, owner_context.workspace_id)

    with pytest.raises(UserStateNotFound):
        owner_state.set_favorite(
            owner_context, "note", str(other_note["id"]), app_key="notes"
        )
    owner_state.record_recent(owner_context, "note", str(owner_note["id"]), app_key="notes")
    notes.delete_note(owner_context, owner_note["id"])
    assert owner_state.list_recent(owner_context) == []
    with sqlite3.connect(identity) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workspace_recent_views WHERE workspace_id=? AND user_id=?",
            (owner_context.workspace_id, owner.id),
        ).fetchone()[0] == 0


def test_favorite_and_recent_ties_have_stable_resource_order(tmp_path):
    identity, _, _, _, context, authorization, _, _ = _system(tmp_path)

    class Resources:
        def resolve_many(self, ctx, grouped):
            return {
                (resource_type, resource_id): ResourceResolution(
                    "visible", "notes", ProviderLink(
                        target=resource_id,
                        href=f"/w/{ctx.workspace_id}/notes?note_id={resource_id}",
                        label=resource_id,
                    ),
                )
                for resource_type, resource_ids in grouped.items()
                for resource_id in resource_ids
            }

    state = WorkspaceUserStateService(identity, authorization, Resources())
    stamp = "2026-08-11T00:00:00Z"
    with sqlite3.connect(identity) as connection:
        app_id = connection.execute("SELECT id FROM apps WHERE key='notes'").fetchone()[0]
        for resource_id in ("2", "1"):
            connection.execute(
                "INSERT INTO workspace_favorites VALUES (?,?,?,?,?,?,?,?,?)",
                (context.workspace_id, context.user_id, app_id, "note", resource_id,
                 resource_id, f"/w/{context.workspace_id}/notes?note_id={resource_id}", stamp, stamp),
            )
            connection.execute(
                "INSERT INTO workspace_recent_views VALUES (?,?,?,?,?,?,?,?)",
                (context.workspace_id, context.user_id, app_id, "note", resource_id,
                 resource_id, f"/w/{context.workspace_id}/notes?note_id={resource_id}", stamp),
            )
    assert [item["resource_id"] for item in state.list_favorites(context)] == ["1", "2"]
    assert [item["resource_id"] for item in state.list_recent(context)] == ["1", "2"]


def test_notes_and_calendar_crud_restore_emit_activity_after_commit(tmp_path):
    identity, _, calendar_path, _, context, authorization, state, notes = _system(tmp_path)
    note = _note(notes, context)
    note = notes.update_note(context, note["id"], NoteUpdate(
        title="Updated note", content="body", note_type="general", is_pinned=False
    ))
    notes.delete_note(context, note["id"])
    notes.restore_note(context, note["id"])

    calendar = CalendarService(
        calendar_path, authorization=authorization, activity_sink=SqliteActivitySink(identity)
    )
    task = calendar.create_task(
        context, title="Task", description="", status="todo", all_day=False,
        start_at="2026-08-11T09:00:00+09:00", due_at="2026-08-11T10:00:00+09:00",
    )
    task = calendar.update_task(
        context, task["id"], title="Updated task", description="", status="doing",
        position=0, all_day=False, start_at=task["start_at"], due_at=task["due_at"],
    )
    calendar.delete_task(context, task["id"])
    calendar.restore_task(context, task["id"])

    actions = [item["action"] for item in state.list_activity(context, limit=20)]
    assert set(actions) == {
        "notes.created", "notes.updated", "notes.deleted", "notes.restored",
        "calendar.created", "calendar.updated", "calendar.deleted", "calendar.restored",
    }
