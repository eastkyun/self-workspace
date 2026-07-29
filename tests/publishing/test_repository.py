from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from publishing.database import (
    database_connection,
    initialize_database,
    verify_pointer_invariants,
    verify_projection_source_of_truth,
)
from publishing.repository import (
    create_publication,
    create_publication_version,
    get_publication,
    get_publication_version,
    list_publications,
    list_publication_versions,
    update_publication_projection_and_pointers,
)

NOW = "2026-07-30T00:00:00Z"


def _create(connection: sqlite3.Connection, *, slug: str = "install") -> dict:
    return create_publication(
        connection,
        note_id=13,
        type_id="manual",
        slug=slug,
        title="Install guide",
        summary="Install safely",
        markdown="# Install",
        source_note_updated_at=NOW,
        created_at=NOW,
    )


def test_create_and_read_initial_immutable_snapshot(tmp_path: Path) -> None:
    path = initialize_database(tmp_path / "publishing.db")
    with database_connection(path) as connection:
        publication = _create(connection)
        version = get_publication_version(connection, publication["id"], 1)

        assert publication["latest_version"] == 1
        assert publication["published_version"] is None
        assert publication["draft_version"] == 1
        assert publication["status"] == "draft"
        assert version["title"] == publication["title"]
        assert version["summary"] == publication["summary"]
        assert version["markdown"] == "# Install"
        assert get_publication(connection, publication["id"]) == publication
        verify_pointer_invariants(connection)
        verify_projection_source_of_truth(connection)


def test_version_and_projection_primitives_preserve_invariants(tmp_path: Path) -> None:
    path = initialize_database(tmp_path / "publishing.db")
    with database_connection(path) as connection:
        publication = _create(connection)
        created = create_publication_version(
            connection,
            publication["id"],
            version=2,
            title="Install guide v2",
            summary="Updated",
            markdown="# Install\n\nUpdated",
            source_note_updated_at="2026-07-30T01:00:00Z",
            created_at="2026-07-30T01:00:00Z",
        )
        updated = update_publication_projection_and_pointers(
            connection,
            publication["id"],
            title=created["title"],
            summary=created["summary"],
            status="draft",
            latest_version=2,
            published_version=None,
            draft_version=2,
            published_at=None,
            archived_at=None,
            updated_at=created["created_at"],
        )

        assert updated["title"] == "Install guide v2"
        assert [row["version"] for row in list_publication_versions(connection, publication["id"])] == [2, 1]
        verify_pointer_invariants(connection)
        verify_projection_source_of_truth(connection)


def test_list_filters_and_deterministic_order(tmp_path: Path) -> None:
    path = initialize_database(tmp_path / "publishing.db")
    with database_connection(path) as connection:
        first = _create(connection, slug="first")
        second = create_publication(
            connection,
            note_id=14,
            type_id="wiki",
            slug="second",
            title="Second",
            summary="",
            markdown="Second",
            source_note_updated_at=NOW,
            created_at="2026-07-30T01:00:00Z",
        )

        assert [row["id"] for row in list_publications(connection)] == [second["id"], first["id"]]
        assert [row["id"] for row in list_publications(connection, note_id=13)] == [first["id"]]
        assert [row["id"] for row in list_publications(connection, type_id="wiki")] == [second["id"]]
        assert len(list_publications(connection, status="draft")) == 2


def test_database_constraints_prevent_duplicate_slug_and_versions(tmp_path: Path) -> None:
    path = initialize_database(tmp_path / "publishing.db")
    with database_connection(path) as connection:
        publication = _create(connection)
        with pytest.raises(sqlite3.IntegrityError):
            _create(connection)
        connection.rollback()

    # The transaction rollback removes the first publication, so recreate it to
    # isolate the version uniqueness assertion.
    with database_connection(path) as connection:
        publication = _create(connection)
        with pytest.raises(sqlite3.IntegrityError):
            create_publication_version(
                connection,
                publication["id"],
                version=1,
                title="Duplicate",
                summary="",
                markdown="Duplicate",
                source_note_updated_at=NOW,
                created_at=NOW,
            )


def test_publication_delete_cascades_only_internal_versions(tmp_path: Path) -> None:
    path = initialize_database(tmp_path / "publishing.db")
    with database_connection(path) as connection:
        publication = _create(connection)
        connection.execute("DELETE FROM publications WHERE id = ?", (publication["id"],))
        assert list_publication_versions(connection, publication["id"]) == []
