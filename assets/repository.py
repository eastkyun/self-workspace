from __future__ import annotations

import sqlite3
from typing import Any, Iterable


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def get_asset(
    connection: sqlite3.Connection,
    asset_id: int,
    workspace_id: str | None = None,
) -> dict[str, Any] | None:
    if workspace_id is None:
        row = connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM assets WHERE id = ? AND workspace_id = ?",
            (asset_id, workspace_id),
        ).fetchone()
    return _dict(row)


def get_asset_by_sha256(
    connection: sqlite3.Connection,
    sha256: str,
    workspace_id: str | None = None,
) -> dict[str, Any] | None:
    if workspace_id is None:
        row = connection.execute("SELECT * FROM assets WHERE sha256 = ?", (sha256,)).fetchone()
    else:
        row = connection.execute(
            "SELECT * FROM assets WHERE sha256 = ? AND workspace_id = ?",
            (sha256, workspace_id),
        ).fetchone()
    return _dict(row)


def create_asset(
    connection: sqlite3.Connection,
    *,
    sha256: str,
    media_type: str,
    byte_size: int,
    storage_key: str,
    original_name: str,
    created_at: str,
    workspace_id: str | None = None,
    uploaded_by: str | None = None,
    public_id: str | None = None,
) -> dict[str, Any]:
    if workspace_id is None:
        cursor = connection.execute(
            """INSERT INTO assets
            (sha256, media_type, byte_size, storage_key, original_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (sha256, media_type, byte_size, storage_key, original_name, created_at),
        )
    else:
        cursor = connection.execute(
            """INSERT INTO assets
            (sha256, media_type, byte_size, storage_key, original_name, created_at,
             workspace_id, uploaded_by, public_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sha256, media_type, byte_size, storage_key, original_name,
                created_at, workspace_id, uploaded_by, public_id,
            ),
        )
    return get_asset(connection, cursor.lastrowid, workspace_id)  # type: ignore[return-value]


def add_reference(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    owner_type: str,
    owner_id: str,
    field: str,
    created_at: str,
    workspace_id: str | None = None,
) -> None:
    if workspace_id is None:
        connection.execute(
            """INSERT OR IGNORE INTO asset_references
            (asset_id, owner_type, owner_id, field, created_at)
            VALUES (?, ?, ?, ?, ?)""",
            (asset_id, owner_type, owner_id, field, created_at),
        )
    else:
        connection.execute(
            """INSERT OR IGNORE INTO asset_references
            (asset_id, owner_type, owner_id, field, created_at, workspace_id)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (asset_id, owner_type, owner_id, field, created_at, workspace_id),
        )


def replace_owner_references(
    connection: sqlite3.Connection,
    *,
    owner_type: str,
    owner_id: str,
    field: str,
    asset_ids: Iterable[int],
    created_at: str,
    workspace_id: str | None = None,
) -> None:
    wanted = tuple(dict.fromkeys(asset_ids))
    if workspace_id is None:
        connection.execute(
            "DELETE FROM asset_references WHERE owner_type = ? AND owner_id = ? AND field = ?",
            (owner_type, owner_id, field),
        )
        rows = [(asset_id, owner_type, owner_id, field, created_at) for asset_id in wanted]
        sql = """INSERT INTO asset_references
        (asset_id, owner_type, owner_id, field, created_at) VALUES (?, ?, ?, ?, ?)"""
    else:
        connection.execute(
            """DELETE FROM asset_references
            WHERE workspace_id = ? AND owner_type = ? AND owner_id = ? AND field = ?""",
            (workspace_id, owner_type, owner_id, field),
        )
        rows = [
            (asset_id, owner_type, owner_id, field, created_at, workspace_id)
            for asset_id in wanted
        ]
        sql = """INSERT INTO asset_references
        (asset_id, owner_type, owner_id, field, created_at, workspace_id)
        VALUES (?, ?, ?, ?, ?, ?)"""
    connection.executemany(sql, rows)


def list_references(
    connection: sqlite3.Connection,
    *,
    owner_type: str,
    owner_id: str,
    field: str,
    workspace_id: str | None = None,
) -> tuple[dict[str, Any], ...]:
    if workspace_id is None:
        rows = connection.execute(
            """SELECT * FROM asset_references
            WHERE owner_type = ? AND owner_id = ? AND field = ? ORDER BY asset_id""",
            (owner_type, owner_id, field),
        ).fetchall()
    else:
        rows = connection.execute(
            """SELECT * FROM asset_references
            WHERE workspace_id = ? AND owner_type = ? AND owner_id = ? AND field = ?
            ORDER BY asset_id""",
            (workspace_id, owner_type, owner_id, field),
        ).fetchall()
    return tuple(dict(row) for row in rows)
