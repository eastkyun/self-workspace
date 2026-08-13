from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
import sqlite3

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


def test_idempotent_create_returns_the_original_note(service: NotesService) -> None:
    first = service.create_note(
        CONTEXT,
        NoteCreate(title="First", content="original"),
        idempotency_key="job-platform:web-collection:run:run-1",
    )
    repeated = service.create_note(
        CONTEXT,
        NoteCreate(title="Ignored", content="retry payload"),
        idempotency_key="job-platform:web-collection:run:run-1",
    )

    assert repeated == first
    with sqlite3.connect(service.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM note_idempotency_keys"
        ).fetchone()[0] == 1


def test_concurrent_idempotent_create_commits_only_one_note(
    service: NotesService,
) -> None:
    key = "job-platform:web-collection:run:run-race"

    def create() -> int:
        return service.create_note(
            CONTEXT, NoteCreate(title="Concurrent"), idempotency_key=key
        )["id"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        note_ids = list(executor.map(lambda _: create(), range(2)))

    assert note_ids[0] == note_ids[1]
    with sqlite3.connect(service.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1


def test_idempotency_key_is_scoped_to_workspace(service: NotesService) -> None:
    other = WorkspaceContext(
        user_id=CONTEXT.user_id,
        workspace_id="00000000-0000-4000-8000-000000000099",
        membership_id=CONTEXT.membership_id,
        role_key=CONTEXT.role_key,
        enabled_app_ids=CONTEXT.enabled_app_ids,
        authorization_version=CONTEXT.authorization_version,
    )
    key = "job-platform:web-collection:run:shared-run-id"

    first = service.create_note(CONTEXT, NoteCreate(title="First"), idempotency_key=key)
    second = service.create_note(other, NoteCreate(title="Second"), idempotency_key=key)

    assert first["id"] != second["id"]


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
