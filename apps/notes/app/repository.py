from __future__ import annotations

import sqlite3
from typing import Any


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def get_note(
    connection: sqlite3.Connection, workspace_id: str, note_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM notes WHERE workspace_id = ? AND id = ? AND deleted_at IS NULL",
        (workspace_id, note_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def get_deleted_note(
    connection: sqlite3.Connection, workspace_id: str, note_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM notes WHERE workspace_id = ? AND id = ? AND deleted_at IS NOT NULL",
        (workspace_id, note_id),
    ).fetchone()
    return row_to_dict(row) if row else None


def create_note(
    connection: sqlite3.Connection,
    workspace_id: str,
    *,
    actor_id: str,
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
            title, content, note_type, is_pinned, created_at, updated_at,
            workspace_id, created_by, updated_by, deleted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            title, content, note_type, int(is_pinned), created_at, updated_at,
            workspace_id, actor_id, actor_id,
        ),
    )
    return get_note(connection, workspace_id, cursor.lastrowid)  # type: ignore[return-value]


def update_note(
    connection: sqlite3.Connection,
    workspace_id: str,
    note_id: int,
    *,
    actor_id: str,
    title: str,
    content: str,
    note_type: str,
    is_pinned: bool,
    updated_at: str,
) -> dict[str, Any] | None:
    cursor = connection.execute(
        """
        UPDATE notes
        SET title = ?, content = ?, note_type = ?, is_pinned = ?,
            updated_at = ?, updated_by = ?
        WHERE workspace_id = ? AND id = ? AND deleted_at IS NULL
        """,
        (
            title, content, note_type, int(is_pinned), updated_at, actor_id,
            workspace_id, note_id,
        ),
    )
    if cursor.rowcount == 0:
        return None
    return get_note(connection, workspace_id, note_id)


def soft_delete_note(
    connection: sqlite3.Connection,
    workspace_id: str,
    note_id: int,
    *,
    actor_id: str,
    deleted_at: str,
) -> bool:
    cursor = connection.execute(
        """UPDATE notes SET deleted_at = ?, updated_at = ?, updated_by = ?
        WHERE workspace_id = ? AND id = ? AND deleted_at IS NULL""",
        (deleted_at, deleted_at, actor_id, workspace_id, note_id),
    )
    return cursor.rowcount > 0


def restore_note(
    connection: sqlite3.Connection,
    workspace_id: str,
    note_id: int,
    *,
    actor_id: str,
    updated_at: str,
) -> dict[str, Any] | None:
    cursor = connection.execute(
        """UPDATE notes SET deleted_at = NULL, updated_at = ?, updated_by = ?
        WHERE workspace_id = ? AND id = ? AND deleted_at IS NOT NULL""",
        (updated_at, actor_id, workspace_id, note_id),
    )
    if cursor.rowcount == 0:
        return None
    return get_note(connection, workspace_id, note_id)


def _escape_like(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_notes(
    connection: sqlite3.Connection,
    workspace_id: str,
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
    clauses = ["notes.workspace_id = ?", "notes.deleted_at IS NULL"]
    parameters: list[Any] = [workspace_id]
    normalized_query = query.strip()
    if normalized_query:
        pattern = f"%{_escape_like(normalized_query)}%"
        clauses.append("(notes.title LIKE ? ESCAPE '\\' OR notes.content LIKE ? ESCAPE '\\')")
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
            "JOIN note_links filtered_link ON filtered_link.note_id = notes.id "
            "AND filtered_link.workspace_id = notes.workspace_id"
        )
        clauses.extend((
            "filtered_link.source_app = ?",
            "filtered_link.source_type = ?",
            "filtered_link.source_id = ?",
        ))
        parameters.extend(source_values)
    parameters.extend((limit, offset))
    rows = connection.execute(
        f"""SELECT notes.* FROM notes {' '.join(joins)}
        WHERE {' AND '.join(clauses)}
        ORDER BY notes.is_pinned DESC, notes.updated_at DESC, notes.id DESC
        LIMIT ? OFFSET ?""",
        parameters,
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def create_link(
    connection: sqlite3.Connection,
    workspace_id: str,
    *,
    note_id: int,
    source_app: str,
    source_type: str,
    source_id: str,
    source_title_snapshot: str,
    created_at: str,
) -> dict[str, Any]:
    cursor = connection.execute(
        """INSERT INTO note_links (
            note_id, source_app, source_type, source_id,
            source_title_snapshot, created_at, workspace_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            note_id, source_app, source_type, source_id,
            source_title_snapshot, created_at, workspace_id,
        ),
    )
    row = connection.execute(
        "SELECT * FROM note_links WHERE workspace_id = ? AND id = ?",
        (workspace_id, cursor.lastrowid),
    ).fetchone()
    return row_to_dict(row)


def list_links(
    connection: sqlite3.Connection, workspace_id: str, note_id: int
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT link.* FROM note_links AS link
        JOIN notes AS note ON note.workspace_id=link.workspace_id AND note.id=link.note_id
        WHERE link.workspace_id = ? AND link.note_id = ? AND note.deleted_at IS NULL
        ORDER BY link.id""",
        (workspace_id, note_id),
    ).fetchall()
    return [row_to_dict(row) for row in rows]


def delete_link(
    connection: sqlite3.Connection, workspace_id: str, note_id: int, link_id: int
) -> bool:
    cursor = connection.execute(
        """DELETE FROM note_links
        WHERE workspace_id = ? AND id = ? AND note_id = ?
        AND EXISTS (
            SELECT 1 FROM notes WHERE notes.workspace_id=note_links.workspace_id
            AND notes.id=note_links.note_id AND notes.deleted_at IS NULL
        )""",
        (workspace_id, link_id, note_id),
    )
    return cursor.rowcount > 0


def get_link(
    connection: sqlite3.Connection, workspace_id: str, note_id: int, link_id: int
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT link.* FROM note_links AS link
        JOIN notes AS note ON note.workspace_id=link.workspace_id AND note.id=link.note_id
        WHERE link.workspace_id = ? AND link.id = ? AND link.note_id = ?
        AND note.deleted_at IS NULL""",
        (workspace_id, link_id, note_id),
    ).fetchone()
    return row_to_dict(row) if row else None
