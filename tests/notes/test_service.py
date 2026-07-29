from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.notes.app.database import initialize_database
from apps.notes.app.schemas import NoteCreate, NoteUpdate
from apps.notes.app.service import NoteNotFoundError, NotesService

NOW = datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC)


@pytest.fixture
def service(tmp_path: Path) -> NotesService:
    path = tmp_path / "notes.db"
    initialize_database(path)
    return NotesService(path, clock=lambda: NOW)


def test_create_trims_title_and_uses_utc_timestamp(service: NotesService) -> None:
    note = service.create_note(NoteCreate(title="  Note  ", content="Body"))

    assert note["title"] == "Note"
    assert note["created_at"] == "2026-07-29T01:02:03+00:00"
    assert note["updated_at"] == note["created_at"]


def test_update_and_delete_not_found_contract(service: NotesService) -> None:
    note = service.create_note(NoteCreate(title="Note"))
    updated = service.update_note(
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

    service.delete_note(note["id"])
    with pytest.raises(NoteNotFoundError):
        service.get_note(note["id"])
    with pytest.raises(NoteNotFoundError):
        service.delete_note(note["id"])


def test_schema_rejects_empty_and_oversized_values() -> None:
    with pytest.raises(ValueError):
        NoteCreate(title="   ")
    with pytest.raises(ValueError):
        NoteCreate(title="x" * 121)
    with pytest.raises(ValueError):
        NoteCreate(title="Note", content="x" * 100001)
    with pytest.raises(ValueError):
        NoteCreate(title="Note", note_type="invalid")
