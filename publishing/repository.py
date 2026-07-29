from __future__ import annotations

import sqlite3
from typing import Any


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def get_publication(
    connection: sqlite3.Connection, publication_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM publications WHERE id = ?", (publication_id,)
    ).fetchone()
    return row_to_dict(row) if row else None


def list_publications(
    connection: sqlite3.Connection,
    *,
    note_id: int | None = None,
    type_id: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    for column, value in (
        ("note_id", note_id),
        ("type_id", type_id),
        ("status", status),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = connection.execute(
        f"""
        SELECT * FROM publications
        {where}
        ORDER BY updated_at DESC, id DESC
        """,
        parameters,
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def create_publication(
    connection: sqlite3.Connection,
    *,
    note_id: int,
    type_id: str,
    slug: str,
    title: str,
    summary: str,
    markdown: str,
    source_note_updated_at: str,
    created_at: str,
) -> dict[str, Any]:
    cursor = connection.execute(
        """
        INSERT INTO publications (
            note_id, type_id, slug, title, summary, status,
            latest_version, published_version, draft_version,
            published_at, archived_at, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, 'draft', 1, NULL, 1, NULL, NULL, ?, ?)
        """,
        (note_id, type_id, slug, title, summary, created_at, created_at),
    )
    publication_id = cursor.lastrowid
    connection.execute(
        """
        INSERT INTO publication_versions (
            publication_id, version, title, summary, markdown,
            source_note_updated_at, created_at
        )
        VALUES (?, 1, ?, ?, ?, ?, ?)
        """,
        (
            publication_id,
            title,
            summary,
            markdown,
            source_note_updated_at,
            created_at,
        ),
    )
    return get_publication(connection, publication_id)  # type: ignore[return-value]


def get_publication_version(
    connection: sqlite3.Connection,
    publication_id: int,
    version: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        """
        SELECT * FROM publication_versions
        WHERE publication_id = ? AND version = ?
        """,
        (publication_id, version),
    ).fetchone()
    return row_to_dict(row) if row else None


def list_publication_versions(
    connection: sqlite3.Connection, publication_id: int
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT * FROM publication_versions
        WHERE publication_id = ?
        ORDER BY version DESC
        """,
        (publication_id,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def create_publication_version(
    connection: sqlite3.Connection,
    publication_id: int,
    *,
    version: int,
    title: str,
    summary: str,
    markdown: str,
    source_note_updated_at: str,
    created_at: str,
) -> dict[str, Any]:
    cursor = connection.execute(
        """
        INSERT INTO publication_versions (
            publication_id, version, title, summary, markdown,
            source_note_updated_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            publication_id,
            version,
            title,
            summary,
            markdown,
            source_note_updated_at,
            created_at,
        ),
    )
    row = connection.execute(
        "SELECT * FROM publication_versions WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    return row_to_dict(row)


def update_publication_projection_and_pointers(
    connection: sqlite3.Connection,
    publication_id: int,
    *,
    title: str,
    summary: str,
    status: str,
    latest_version: int,
    published_version: int | None,
    draft_version: int | None,
    published_at: str | None,
    archived_at: str | None,
    updated_at: str,
) -> dict[str, Any] | None:
    cursor = connection.execute(
        """
        UPDATE publications
        SET title = ?, summary = ?, status = ?, latest_version = ?,
            published_version = ?, draft_version = ?, published_at = ?,
            archived_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            title,
            summary,
            status,
            latest_version,
            published_version,
            draft_version,
            published_at,
            archived_at,
            updated_at,
            publication_id,
        ),
    )
    if cursor.rowcount == 0:
        return None
    return get_publication(connection, publication_id)
