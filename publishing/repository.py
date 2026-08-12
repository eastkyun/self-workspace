from __future__ import annotations

import sqlite3
from typing import Any


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def get_publication(
    connection: sqlite3.Connection,
    publication_id: int,
    workspace_id: str | None = None,
) -> dict[str, Any] | None:
    if workspace_id is None:
        row = connection.execute(
            "SELECT * FROM publications WHERE id = ?", (publication_id,)
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM publications WHERE id = ? AND workspace_id = ?",
            (publication_id, workspace_id),
        ).fetchone()
    return row_to_dict(row) if row else None


def get_publication_by_slug(
    connection: sqlite3.Connection,
    *,
    type_id: str,
    slug: str,
    workspace_id: str | None = None,
    public_only: bool = False,
) -> dict[str, Any] | None:
    clauses = ["type_id = ?", "slug = ?"]
    parameters: list[Any] = [type_id, slug]
    if workspace_id is not None:
        clauses.append("workspace_id = ?")
        parameters.append(workspace_id)
    if public_only:
        clauses.append("visibility = 'PUBLIC'")
    row = connection.execute(
        f"SELECT * FROM publications WHERE {' AND '.join(clauses)}",
        parameters,
    ).fetchone()
    return row_to_dict(row) if row else None


def get_publication_by_public_id(
    connection: sqlite3.Connection, public_id: str
) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM publications WHERE public_id = ? AND visibility = 'PUBLIC'",
        (public_id,),
    ).fetchone()
    return row_to_dict(row) if row else None


def get_publication_by_route_alias(
    connection: sqlite3.Connection, route_path: str
) -> dict[str, Any] | None:
    row = connection.execute(
        """SELECT p.* FROM publication_route_aliases AS alias
        JOIN publications AS p ON p.id = alias.publication_id
        WHERE alias.route_path = ? AND p.visibility = 'PUBLIC'""",
        (route_path,),
    ).fetchone()
    return row_to_dict(row) if row else None


def add_publication_route_aliases(
    connection: sqlite3.Connection,
    publication: dict[str, Any],
    route_paths: tuple[str, ...],
) -> None:
    connection.executemany(
        """INSERT INTO publication_route_aliases
        (publication_id, workspace_id, route_path, created_at)
        VALUES (?, ?, ?, ?)""",
        (
            (
                publication["id"],
                publication["workspace_id"],
                route_path,
                publication["created_at"],
            )
            for route_path in route_paths
        ),
    )


def list_publications(
    connection: sqlite3.Connection,
    *,
    note_id: int | None = None,
    type_id: str | None = None,
    status: str | None = None,
    workspace_id: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    for column, value in (
        ("workspace_id", workspace_id),
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
    workspace_id: str | None = None,
    created_by: str | None = None,
    public_id: str | None = None,
) -> dict[str, Any]:
    if workspace_id is None:
        cursor = connection.execute(
            """INSERT INTO publications (
                note_id, type_id, slug, title, summary, status,
                latest_version, published_version, draft_version,
                published_at, archived_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'draft', 1, NULL, 1, NULL, NULL, ?, ?)""",
            (note_id, type_id, slug, title, summary, created_at, created_at),
        )
    else:
        cursor = connection.execute(
            """INSERT INTO publications (
                note_id, type_id, slug, title, summary, status,
                latest_version, published_version, draft_version,
                published_at, archived_at, created_at, updated_at,
                workspace_id, created_by, visibility, public_id)
            VALUES (?, ?, ?, ?, ?, 'draft', 1, NULL, 1, NULL, NULL, ?, ?, ?, ?, 'PUBLIC', ?)""",
            (
                note_id, type_id, slug, title, summary, created_at, created_at,
                workspace_id, created_by, public_id,
            ),
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
    return get_publication(connection, publication_id, workspace_id)  # type: ignore[return-value]


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


def get_projection_version(
    connection: sqlite3.Connection,
    publication: dict[str, Any],
) -> dict[str, Any] | None:
    version = (
        publication["draft_version"]
        or publication["published_version"]
        or publication["latest_version"]
    )
    return get_publication_version(connection, publication["id"], version)


def get_public_version(
    connection: sqlite3.Connection,
    publication: dict[str, Any],
) -> dict[str, Any] | None:
    version = publication["published_version"]
    if publication["status"] != "published" or version is None:
        return None
    return get_publication_version(connection, publication["id"], version)


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
    workspace_id: str | None = None,
) -> dict[str, Any] | None:
    values = (
        title, summary, status, latest_version, published_version,
        draft_version, published_at, archived_at, updated_at, publication_id,
    )
    if workspace_id is None:
        cursor = connection.execute(
            """UPDATE publications
            SET title = ?, summary = ?, status = ?, latest_version = ?,
                published_version = ?, draft_version = ?, published_at = ?,
                archived_at = ?, updated_at = ?
            WHERE id = ?""",
            values,
        )
    else:
        cursor = connection.execute(
            """UPDATE publications
            SET title = ?, summary = ?, status = ?, latest_version = ?,
                published_version = ?, draft_version = ?, published_at = ?,
                archived_at = ?, updated_at = ?
            WHERE id = ? AND workspace_id = ?""",
            (*values, workspace_id),
        )
    if cursor.rowcount == 0:
        return None
    return get_publication(connection, publication_id, workspace_id)
