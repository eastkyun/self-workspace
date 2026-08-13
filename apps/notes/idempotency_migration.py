"""Stopped-service Notes v2 -> v3 idempotency migration."""

from __future__ import annotations

from shared.migrations import MigrationStep, get_user_version


def _apply(connection, _context) -> None:
    connection.execute(
        "CREATE UNIQUE INDEX idx_notes_workspace_id ON notes(workspace_id, id)"
    )
    connection.execute(
        """CREATE TABLE note_idempotency_keys (
        workspace_id TEXT NOT NULL,
        key TEXT NOT NULL,
        note_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(workspace_id, key),
        FOREIGN KEY(workspace_id, note_id)
            REFERENCES notes(workspace_id, id) ON DELETE CASCADE,
        CHECK(length(key) BETWEEN 1 AND 255))"""
    )
    connection.execute(
        """CREATE INDEX idx_note_idempotency_note
        ON note_idempotency_keys(workspace_id, note_id)"""
    )


def _verify(connection, _context) -> None:
    if get_user_version(connection) != 3:
        raise RuntimeError("Notes idempotency migration did not reach v3")
    columns = {
        str(row[1]): row
        for row in connection.execute("PRAGMA table_info(note_idempotency_keys)")
    }
    if set(columns) != {"workspace_id", "key", "note_id", "created_at"}:
        raise RuntimeError("Notes v3 idempotency columns are incompatible")
    if any(columns[name][3] != 1 for name in columns):
        raise RuntimeError("Notes v3 idempotency columns must be NOT NULL")
    index_columns = {
        tuple(
            item[2]
            for item in connection.execute(f"PRAGMA index_info({row[1]})")
        )
        for row in connection.execute("PRAGMA index_list(note_idempotency_keys)")
    }
    if ("workspace_id", "key") not in index_columns:
        raise RuntimeError("Notes v3 idempotency uniqueness is missing")
    if ("workspace_id", "note_id") not in index_columns:
        raise RuntimeError("Notes v3 idempotency lookup index is missing")


MIGRATIONS = (MigrationStep(2, 3, _apply, _verify),)
TARGET_VERSION = 3
