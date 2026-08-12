from datetime import UTC, datetime

import pytest

from access.context import PermissionSnapshot, WorkspaceContext
from access.errors import PermissionDenied
from apps.notes.app.database import initialize_database
from apps.notes.app.internal_link_provider import NotesInternalLinkProvider
from apps.notes.app.schemas import NoteCreate, NoteUpdate
from apps.notes.app.service import NoteNotFoundError, NotesService


def context(workspace: str, user: str, role: str = "member") -> WorkspaceContext:
    return WorkspaceContext(
        user_id=user,
        workspace_id=workspace,
        membership_id=f"membership-{workspace}-{user}",
        role_key=role,
        enabled_app_ids=("notes",),
        authorization_version=1,
    )


class Policy:
    def __init__(self, permissions: set[str]) -> None:
        self.permissions = frozenset(permissions)

    def snapshot(self, ctx: WorkspaceContext) -> PermissionSnapshot:
        return PermissionSnapshot(
            ctx.workspace_id, ctx.membership_id, ctx.role_key, 1,
            (("notes", "notes-app"),), self.permissions, frozenset(),
            frozenset(), frozenset(), frozenset({"notes"}),
        )

    def require(self, ctx, permission_code=None, resource_owner_id=None, *, app_key=None, snapshot=None):
        if app_key == "notes" and "notes.read_all" not in self.permissions:
            from access.errors import AppNotAvailable
            raise AppNotAvailable()
        if permission_code and not self.authorize(
            ctx, permission_code, resource_owner_id, snapshot=snapshot
        ):
            raise PermissionDenied()
        return self.snapshot(ctx)

    def authorize(self, ctx, permission_code, resource_owner_id=None, *, snapshot=None):
        if permission_code not in self.permissions:
            return False
        return not permission_code.endswith("_own") or resource_owner_id == ctx.user_id


@pytest.fixture
def service(tmp_path):
    path = initialize_database(tmp_path / "notes.db")
    policy = Policy({
        "notes.read_all", "notes.create", "notes.update_own",
        "notes.delete_own", "notes.manage",
    })
    return NotesService(
        path,
        authorization=policy,
        clock=lambda: datetime(2026, 8, 3, tzinfo=UTC),
    )


def test_cross_workspace_idor_is_not_found_on_every_active_resource_surface(service):
    workspace_a = context("workspace-a", "user-a")
    workspace_b = context("workspace-b", "user-b")
    note = service.create_note(workspace_a, NoteCreate(title="A secret"))

    with pytest.raises(NoteNotFoundError):
        service.get_note(workspace_b, note["id"])
    with pytest.raises(NoteNotFoundError):
        service.update_note(workspace_b, note["id"], NoteUpdate(title="stolen"))
    with pytest.raises(NoteNotFoundError):
        service.delete_note(workspace_b, note["id"])
    assert service.list_notes(workspace_b) == []


def test_own_permissions_use_created_by_and_actor_fields_are_maintained(service):
    owner = context("workspace-a", "creator")
    other = context("workspace-a", "other")
    note = service.create_note(owner, NoteCreate(title="Owned"))
    assert note["created_by"] == note["updated_by"] == owner.user_id

    with pytest.raises(PermissionDenied):
        service.update_note(other, note["id"], NoteUpdate(title="Denied"))
    updated = service.update_note(owner, note["id"], NoteUpdate(title="Allowed"))
    assert updated["updated_by"] == owner.user_id


def test_soft_delete_hides_note_and_manage_restore_recovers_it(service):
    ctx = context("workspace-a", "creator")
    note = service.create_note(ctx, NoteCreate(title="Recoverable"))
    service.delete_note(ctx, note["id"])
    with pytest.raises(NoteNotFoundError):
        service.get_note(ctx, note["id"])
    assert service.list_notes(ctx) == []

    restored = service.restore_note(ctx, note["id"])
    assert restored["id"] == note["id"]
    assert restored["deleted_at"] is None


def test_restore_is_manage_only(tmp_path):
    path = initialize_database(tmp_path / "notes.db")
    full = Policy({"notes.read_all", "notes.create", "notes.delete_own"})
    service = NotesService(path, authorization=full)
    ctx = context("workspace-a", "creator")
    note = service.create_note(ctx, NoteCreate(title="Deleted"))
    service.delete_note(ctx, note["id"])
    with pytest.raises(PermissionDenied):
        service.restore_note(ctx, note["id"])


def test_internal_link_provider_is_workspace_scoped_and_hides_deleted(service):
    workspace_a = context("workspace-a", "user-a")
    workspace_b = context("workspace-b", "user-b")
    note = service.create_note(workspace_a, NoteCreate(title="Private"))

    assert NotesInternalLinkProvider(
        service.database_path, workspace_b.workspace_id
    ).resolve_many((str(note["id"]),)) == {}
    provider = NotesInternalLinkProvider(service.database_path, workspace_a.workspace_id)
    assert provider.resolve_many((str(note["id"]),))[str(note["id"])].href.startswith(
        f"/w/{workspace_a.workspace_id}/notes"
    )

    service.delete_note(workspace_a, note["id"])
    assert provider.resolve_many((str(note["id"]),)) == {}
