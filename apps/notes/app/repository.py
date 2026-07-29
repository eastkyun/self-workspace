from __future__ import annotations

import sqlite3
from typing import Any


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def get_note(connection: sqlite3.Connection, note_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM notes WHERE id = ?",
        (note_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


def create_note(
    connection: sqlite3.Connection,
    *,
    title: str,
    content: str,
    note_type: str,
    is_pinned: bool,
    created_at: str,
    updated_at: str,
) -> dict[str, Any]:
    cursor = connection.execute(
        """
        INSERT INTO notes (
            title, content, note_type, is_pinned, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (title, content, note_type, int(is_pinned), created_at, updated_at),
    )
    return get_note(connection, cursor.lastrowid)  # type: ignore[return-value]


def update_note(
    connection: sqlite3.Connection,
    note_id: int,
    *,
    title: str,
    content: str,
    note_type: str,
    is_pinned: bool,
    updated_at: str,
) -> dict[str, Any] | None:
    cursor = connection.execute(
        """
        UPDATE notes
        SET title = ?, content = ?, note_type = ?, is_pinned = ?, updated_at = ?
        WHERE id = ?
        """,
        (title, content, note_type, int(is_pinned), updated_at, note_id),
    )
    if cursor.rowcount == 0:
        return None
    return get_note(connection, note_id)


def delete_note(connection: sqlite3.Connection, note_id: int) -> bool:
    cursor = connection.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return cursor.rowcount > 0


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_notes(
    connection: sqlite3.Connection,
    *,
    query: str = "",
    note_type: str | None = None,
    pinned: bool | None = None,
    source_app: str | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    joins: list[str] = []
    clauses: list[str] = []
    parameters: list[Any] = []
    normalized_query = query.strip()
    if normalized_query:
        pattern = f"%{_escape_like(normalized_query)}%"
        clauses.append(
            "(notes.title LIKE ? ESCAPE '\\' OR notes.content LIKE ? ESCAPE '\\')"
        )
        parameters.extend((pattern, pattern))
    if note_type is not None:
        clauses.append("notes.note_type = ?")
        parameters.append(note_type)
    if pinned is not None:
        clauses.append("notes.is_pinned = ?")
        parameters.append(int(pinned))
    source_values = (source_app, source_type, source_id)
    if any(value is not None for value in source_values):
        if not all(value is not None for value in source_values):
            raise ValueError("source_app, source_type, and source_id are required together")
        joins.append(
            "JOIN note_links filtered_link ON filtered_link.note_id = notes.id"
        )
        clauses.extend(
            (
                "filtered_link.source_app = ?",
                "filtered_link.source_type = ?",
                "filtered_link.source_id = ?",
            )
        )
        parameters.extend(source_values)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    join = " ".join(joins)
    parameters.extend((limit, offset))
    rows = connection.execute(
        f"""
        SELECT notes.*
        FROM notes
        {join}
        {where}
        ORDER BY notes.is_pinned DESC, notes.updated_at DESC, notes.id DESC
        LIMIT ? OFFSET ?
        """,
        parameters,
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def create_link(
    connection: sqlite3.Connection,
    *,
    note_id: int,
    source_app: str,
    source_type: str,
    source_id: str,
    source_title_snapshot: str,
    created_at: str,
) -> dict[str, Any]:
    cursor = connection.execute(
        """
        INSERT INTO note_links (
            note_id, source_app, source_type, source_id,
            source_title_snapshot, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            note_id,
            source_app,
            source_type,
            source_id,
            source_title_snapshot,
            created_at,
        ),
    )
    row = connection.execute(
        "SELECT * FROM note_links WHERE id = ?",
        (cursor.lastrowid,),
    ).fetchone()
    return row_to_dict(row)


def list_links(
    connection: sqlite3.Connection,
    note_id: int,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM note_links WHERE note_id = ? ORDER BY id",
        (note_id,),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def delete_link(
    connection: sqlite3.Connection,
    note_id: int,
    link_id: int,
) -> bool:
    cursor = connection.execute(
        "DELETE FROM note_links WHERE id = ? AND note_id = ?",
        (link_id, note_id),
    )
    return cursor.rowcount > 0


def get_link(
    connection: sqlite3.Connection,
    note_id: int,
    link_id: int,
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM note_links WHERE id = ? AND note_id = ?",
        (link_id, note_id),
    ).fetchone()
    return row_to_dict(row) if row else None
