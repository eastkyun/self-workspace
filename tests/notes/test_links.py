import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.calendar.app.main import initialize_database as initialize_calendar
from apps.calendar.app.source_provider import CalendarEventSourceProvider
from apps.notes.app.main import create_app
from shared.source_references import SourceReferenceResolver


def add_event(database_path: Path, *, event_id: int = 13, title: str = "회의") -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            INSERT INTO tasks (
                id, title, description, status, position, all_day,
                start_at, due_at, created_at, updated_at
            )
            VALUES (?, ?, '', 'todo', 0, 0, ?, ?, ?, ?)
            """,
            (
                event_id,
                title,
                "2026-07-29T09:00:00+09:00",
                "2026-07-29T10:00:00+09:00",
                "2026-07-29T00:00:00+00:00",
                "2026-07-29T00:00:00+00:00",
            ),
        )


@pytest.fixture
def linked_client(tmp_path: Path):
    calendar_path = tmp_path / "calendar.db"
    notes_path = tmp_path / "notes.db"
    initialize_calendar(calendar_path, create_backup=False)
    add_event(calendar_path)
    resolver = SourceReferenceResolver(
        {("calendar", "event"): CalendarEventSourceProvider(calendar_path)}
    )
    with TestClient(
        create_app(notes_path, source_resolver=resolver)
    ) as client:
        yield client, calendar_path, notes_path


def create_note(client: TestClient, *, links: list[dict] | None = None) -> dict:
    response = client.post(
        "/api/notes",
        json={
            "title": "회의록",
            "content": "",
            "note_type": "meeting_minutes",
            "is_pinned": False,
            "links": links or [],
        },
    )
    assert response.status_code == 201
    return response.json()


def calendar_link(source_id: str = "13") -> dict[str, str]:
    return {
        "source_app": "calendar",
        "source_type": "event",
        "source_id": source_id,
    }


def test_create_list_duplicate_and_unlink_contract(linked_client) -> None:
    client, _, _ = linked_client
    note = create_note(client)

    created = client.post(
        f"/api/notes/{note['id']}/links",
        json=calendar_link(),
    )
    assert created.status_code == 201
    assert created.json()["source_title_snapshot"] == "회의"
    assert created.json()["orphan"] is False

    duplicate = client.post(
        f"/api/notes/{note['id']}/links",
        json=calendar_link(),
    )
    assert duplicate.status_code == 409
    assert "UNIQUE" not in duplicate.text

    links = client.get(f"/api/notes/{note['id']}/links").json()
    assert len(links) == 1
    assert client.delete(
        f"/api/notes/{note['id']}/links/{links[0]['id']}"
    ).status_code == 204
    assert client.get(f"/api/notes/{note['id']}/links").json() == []


def test_create_note_and_link_is_atomic(linked_client) -> None:
    client, _, notes_path = linked_client
    created = create_note(client, links=[calendar_link()])
    assert client.get(f"/api/notes/{created['id']}/links").json()[0]["orphan"] is False

    duplicate = client.post(
        "/api/notes",
        json={
            "title": "Duplicate",
            "links": [calendar_link(), calendar_link()],
        },
    )
    assert duplicate.status_code == 409
    with sqlite3.connect(notes_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM notes WHERE title = 'Duplicate'"
        ).fetchone()[0] == 0


@pytest.mark.parametrize(
    ("payload", "status"),
    [
        ({"source_app": "Calendar", "source_type": "event", "source_id": "13"}, 422),
        ({"source_app": "calendar", "source_type": "task", "source_id": "13"}, 422),
        ({"source_app": "calendar", "source_type": "event", "source_id": "bad"}, 422),
        ({"source_app": "calendar", "source_type": "event", "source_id": "999"}, 404),
    ],
)
def test_source_validation_error_contract(linked_client, payload, status) -> None:
    client, _, _ = linked_client
    note = create_note(client)
    response = client.post(f"/api/notes/{note['id']}/links", json=payload)
    assert response.status_code == status


@pytest.mark.parametrize(
    "query",
    [
        "source_app=Calendar&source_type=event&source_id=13",
        "source_app=calendar&source_type=task&source_id=13",
        "source_app=calendar&source_type=event&source_id=bad",
    ],
)
def test_source_filter_rejects_malformed_or_unsupported_reference(
    linked_client,
    query: str,
) -> None:
    client, _, _ = linked_client
    assert client.get(f"/api/notes?{query}").status_code == 422


def test_source_filter_allows_missing_well_formed_reference(linked_client) -> None:
    client, _, _ = linked_client
    response = client.get(
        "/api/notes?source_app=calendar&source_type=event&source_id=999"
    )
    assert response.status_code == 200
    assert response.json() == []


def test_event_deletion_preserves_link_snapshot_and_marks_orphan(
    linked_client,
) -> None:
    client, calendar_path, notes_path = linked_client
    note = create_note(client, links=[calendar_link()])
    before = client.get(f"/api/notes/{note['id']}/links").json()[0]

    with sqlite3.connect(calendar_path) as connection:
        connection.execute("DELETE FROM tasks WHERE id = 13")

    after = client.get(f"/api/notes/{note['id']}/links").json()[0]
    assert client.get(f"/api/notes/{note['id']}").status_code == 200
    assert after["orphan"] is True
    assert after["source_title_snapshot"] == before["source_title_snapshot"] == "회의"
    with sqlite3.connect(notes_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM note_links").fetchone()[0] == 1


def test_note_soft_delete_preserves_internal_link_for_restore(linked_client) -> None:
    client, calendar_path, notes_path = linked_client
    note = create_note(client, links=[calendar_link()])
    assert client.delete(f"/api/notes/{note['id']}").status_code == 204

    with sqlite3.connect(notes_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM note_links").fetchone()[0] == 1
        assert connection.execute(
            "SELECT deleted_at IS NOT NULL FROM notes WHERE id=?", (note["id"],)
        ).fetchone()[0] == 1
    with sqlite3.connect(calendar_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
