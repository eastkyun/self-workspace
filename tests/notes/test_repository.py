from pathlib import Path
import sqlite3

import pytest

from apps.notes.app.database import database_connection, initialize_database
from apps.notes.app.repository import (
    create_link,
    create_note,
    soft_delete_note,
    list_links,
    list_notes,
    update_note,
)

NOW = "2026-07-29T00:00:00+00:00"
WORKSPACE = "00000000-0000-4000-8000-000000000001"
ACTOR = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def connection(tmp_path: Path):
    path = tmp_path / "notes.db"
    initialize_database(path)
    with database_connection(path) as active:
        yield active


def add_note(connection, title: str, *, content: str = "", pinned: bool = False):
    return create_note(
        connection,
        WORKSPACE,
        actor_id=ACTOR,
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

    assert [note["id"] for note in list_notes(connection, WORKSPACE)] == [
        pinned["id"],
        first["id"],
    ]
    updated = update_note(
        connection,
        WORKSPACE,
        first["id"],
        actor_id=ACTOR,
        title="Updated",
        content="Body",
        note_type="research_note",
        is_pinned=False,
        updated_at="2026-07-30T00:00:00+00:00",
    )
    assert updated["title"] == "Updated"
    assert soft_delete_note(connection, WORKSPACE, first["id"], actor_id=ACTOR, deleted_at=NOW) is True
    assert soft_delete_note(connection, WORKSPACE, first["id"], actor_id=ACTOR, deleted_at=NOW) is False


def test_search_treats_sql_wildcards_as_literals(connection) -> None:
    add_note(connection, "100% exact", content="under_score")
    add_note(connection, "Other", content="plain")

    assert [note["title"] for note in list_notes(connection, WORKSPACE, query="%")] == [
        "100% exact"
    ]
    assert [note["title"] for note in list_notes(connection, WORKSPACE, query="_")] == [
        "100% exact"
    ]
    assert len(list_notes(connection, WORKSPACE, query="   ")) == 2


def test_pagination_and_source_filter(connection) -> None:
    first = add_note(connection, "First")
    second = add_note(connection, "Second")
    create_link(
        connection,
        WORKSPACE,
        note_id=first["id"],
        source_app="calendar",
        source_type="event",
        source_id="13",
        source_title_snapshot="Meeting",
        created_at=NOW,
    )

    assert [note["id"] for note in list_notes(connection, WORKSPACE, limit=1)] == [second["id"]]
    assert [
        note["id"]
        for note in list_notes(
            connection,
            WORKSPACE,
            source_app="calendar",
            source_type="event",
            source_id="13",
        )
    ] == [first["id"]]
    with pytest.raises(ValueError, match="required together"):
        list_notes(connection, WORKSPACE, source_app="calendar")


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
    create_link(connection, WORKSPACE, **payload)

    with pytest.raises(sqlite3.IntegrityError):
        create_link(connection, WORKSPACE, **payload)

    assert len(list_links(connection, WORKSPACE, note["id"])) == 1
    soft_delete_note(connection, WORKSPACE, note["id"], actor_id=ACTOR, deleted_at=NOW)
    # Soft delete preserves stored links for restore while active queries hide them.
    assert list_links(connection, WORKSPACE, note["id"]) == []
    assert connection.execute(
        "SELECT COUNT(*) FROM note_links WHERE workspace_id=?", (WORKSPACE,)
    ).fetchone()[0] == 1
