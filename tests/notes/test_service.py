from datetime import UTC, datetime
from pathlib import Path

import pytest

from access.context import PermissionSnapshot, WorkspaceContext
from access.errors import PermissionDenied
from apps.notes.app.database import initialize_database
from apps.notes.app.schemas import NoteCreate, NoteUpdate
from apps.notes.app.service import NoteNotFoundError, NotesService

NOW = datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC)
CONTEXT = WorkspaceContext(
    user_id="00000000-0000-4000-8000-000000000001",
    workspace_id="00000000-0000-4000-8000-000000000002",
    membership_id="00000000-0000-4000-8000-000000000003",
    role_key="owner",
    enabled_app_ids=("notes",),
    authorization_version=1,
)


class AllowNotes:
    permissions = frozenset({
        "notes.read_all", "notes.create", "notes.update_all", "notes.delete_all", "notes.manage"
    })

    def require(self, context, permission_code=None, resource_owner_id=None, *, app_key=None, snapshot=None):
        if permission_code and permission_code not in self.permissions:
            raise PermissionDenied()
        return PermissionSnapshot(
            context.workspace_id, context.membership_id, context.role_key, 1,
            (("notes", "notes"),), self.permissions, frozenset(), frozenset(),
            frozenset(), frozenset({"notes"}),
        )

    def authorize(self, context, permission_code, resource_owner_id=None, *, snapshot=None):
        return permission_code in self.permissions


@pytest.fixture
def service(tmp_path: Path) -> NotesService:
    path = tmp_path / "notes.db"
    initialize_database(path)
    return NotesService(path, authorization=AllowNotes(), clock=lambda: NOW)


def test_create_trims_title_and_uses_utc_timestamp(service: NotesService) -> None:
    note = service.create_note(CONTEXT, NoteCreate(title="  Note  ", content="Body"))

    assert note["title"] == "Note"
    assert note["created_at"] == "2026-07-29T01:02:03+00:00"
    assert note["updated_at"] == note["created_at"]


def test_update_and_delete_not_found_contract(service: NotesService) -> None:
    note = service.create_note(CONTEXT, NoteCreate(title="Note"))
    updated = service.update_note(
        CONTEXT,
        note["id"],
        NoteUpdate(
            title="Updated",
            content="Research",
            note_type="research_note",
            is_pinned=True,
        ),
    )
    assert updated["is_pinned"] == 1
    assert updated["note_type"] == "research_note"

    service.delete_note(CONTEXT, note["id"])
    with pytest.raises(NoteNotFoundError):
        service.get_note(CONTEXT, note["id"])
    with pytest.raises(NoteNotFoundError):
        service.delete_note(CONTEXT, note["id"])


def test_schema_rejects_empty_and_oversized_values() -> None:
    with pytest.raises(ValueError):
        NoteCreate(title="   ")
    with pytest.raises(ValueError):
        NoteCreate(title="x" * 121)
    with pytest.raises(ValueError):
        NoteCreate(title="Note", content="x" * 100001)
    with pytest.raises(ValueError):
        NoteCreate(title="Note", note_type="invalid")
