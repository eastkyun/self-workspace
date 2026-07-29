from pathlib import Path
import sqlite3

import pytest

from apps.notes.app.database import database_connection, initialize_database
from apps.notes.app.repository import (
    create_link,
    create_note,
    delete_note,
    list_links,
    list_notes,
    update_note,
)

NOW = "2026-07-29T00:00:00+00:00"


@pytest.fixture
def connection(tmp_path: Path):
    path = tmp_path / "notes.db"
    initialize_database(path)
    with database_connection(path) as active:
        yield active


def add_note(connection, title: str, *, content: str = "", pinned: bool = False):
    return create_note(
        connection,
        title=title,
        content=content,
        note_type="general",
        is_pinned=pinned,
        created_at=NOW,
        updated_at=NOW,
    )


def test_crud_and_deterministic_ordering(connection) -> None:
    first = add_note(connection, "First")
    pinned = add_note(connection, "Pinned", pinned=True)

    assert [note["id"] for note in list_notes(connection)] == [
        pinned["id"],
        first["id"],
    ]
    updated = update_note(
        connection,
        first["id"],
        title="Updated",
        content="Body",
        note_type="research_note",
        is_pinned=False,
        updated_at="2026-07-30T00:00:00+00:00",
    )
    assert updated["title"] == "Updated"
    assert delete_note(connection, first["id"]) is True
    assert delete_note(connection, first["id"]) is False


def test_search_treats_sql_wildcards_as_literals(connection) -> None:
    add_note(connection, "100% exact", content="under_score")
    add_note(connection, "Other", content="plain")

    assert [note["title"] for note in list_notes(connection, query="%")] == [
        "100% exact"
    ]
    assert [note["title"] for note in list_notes(connection, query="_")] == [
        "100% exact"
    ]
    assert len(list_notes(connection, query="   ")) == 2


def test_pagination_and_source_filter(connection) -> None:
    first = add_note(connection, "First")
    second = add_note(connection, "Second")
    create_link(
        connection,
        note_id=first["id"],
        source_app="calendar",
        source_type="event",
        source_id="13",
        source_title_snapshot="Meeting",
        created_at=NOW,
    )

    assert [note["id"] for note in list_notes(connection, limit=1)] == [second["id"]]
    assert [
        note["id"]
        for note in list_notes(
            connection,
            source_app="calendar",
            source_type="event",
            source_id="13",
        )
    ] == [first["id"]]
    with pytest.raises(ValueError, match="required together"):
        list_notes(connection, source_app="calendar")


def test_link_uniqueness_and_note_delete_cascade(connection) -> None:
    note = add_note(connection, "Linked")
    payload = dict(
        note_id=note["id"],
        source_app="calendar",
        source_type="event",
        source_id="13",
        source_title_snapshot="Meeting",
        created_at=NOW,
    )
    create_link(connection, **payload)

    with pytest.raises(sqlite3.IntegrityError):
        create_link(connection, **payload)

    assert len(list_links(connection, note["id"])) == 1
    delete_note(connection, note["id"])
    assert list_links(connection, note["id"]) == []
