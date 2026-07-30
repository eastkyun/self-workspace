from __future__ import annotations

import sqlite3
from typing import Any, Iterable


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def get_asset(connection: sqlite3.Connection, asset_id: int) -> dict[str, Any] | None:
    return _dict(connection.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone())


def get_asset_by_sha256(connection: sqlite3.Connection, sha256: str) -> dict[str, Any] | None:
    return _dict(connection.execute("SELECT * FROM assets WHERE sha256 = ?", (sha256,)).fetchone())


def create_asset(
    connection: sqlite3.Connection,
    *,
    sha256: str,
    media_type: str,
    byte_size: int,
    storage_key: str,
    original_name: str,
    created_at: str,
) -> dict[str, Any]:
    cursor = connection.execute(
        """
        INSERT INTO assets (sha256, media_type, byte_size, storage_key, original_name, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (sha256, media_type, byte_size, storage_key, original_name, created_at),
    )
    return get_asset(connection, cursor.lastrowid)  # type: ignore[return-value]


def add_reference(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    owner_type: str,
    owner_id: str,
    field: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO asset_references
            (asset_id, owner_type, owner_id, field, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (asset_id, owner_type, owner_id, field, created_at),
    )


def replace_owner_references(
    connection: sqlite3.Connection,
    *,
    owner_type: str,
    owner_id: str,
    field: str,
    asset_ids: Iterable[int],
    created_at: str,
) -> None:
    wanted = tuple(dict.fromkeys(asset_ids))
    connection.execute(
        "DELETE FROM asset_references WHERE owner_type = ? AND owner_id = ? AND field = ?",
        (owner_type, owner_id, field),
    )
    connection.executemany(
        """
        INSERT INTO asset_references (asset_id, owner_type, owner_id, field, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(asset_id, owner_type, owner_id, field, created_at) for asset_id in wanted],
    )


def list_references(
    connection: sqlite3.Connection,
    *,
    owner_type: str,
    owner_id: str,
    field: str,
) -> tuple[dict[str, Any], ...]:
    rows = connection.execute(
        """
        SELECT * FROM asset_references
        WHERE owner_type = ? AND owner_id = ? AND field = ?
        ORDER BY asset_id
        """,
        (owner_type, owner_id, field),
    ).fetchall()
    return tuple(dict(row) for row in rows)
