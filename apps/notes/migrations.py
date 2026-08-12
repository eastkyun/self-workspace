"""Offline Notes v1 -> v2 workspace migration (staging copies only)."""

from __future__ import annotations

from shared.migrations import MigrationStep, get_user_version
from shared.migrations.domain_context import all_index_columns, assert_backfilled, index_columns, required_uuid, restore_sequence, sequence_value


def _apply(connection, context) -> None:
    workspace_id = required_uuid(context, "workspace_id")
    actor_id = required_uuid(context, "actor_id")
    sequences = {table: sequence_value(connection, table) for table in ("notes", "note_links")}
    connection.execute("ALTER TABLE note_links RENAME TO note_links_v1")
    connection.execute("ALTER TABLE notes RENAME TO notes_v1")
    connection.execute(
        """CREATE TABLE notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '', note_type TEXT NOT NULL DEFAULT 'general',
        is_pinned INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        workspace_id TEXT NOT NULL, created_by TEXT NOT NULL, updated_by TEXT NOT NULL, deleted_at TEXT,
        CHECK(length(trim(title)) BETWEEN 1 AND 120), CHECK(length(content) <= 100000),
        CHECK(note_type IN ('general', 'meeting_minutes', 'research_note')),
        CHECK(is_pinned IN (0, 1)))"""
    )
    connection.execute(
        """INSERT INTO notes
        SELECT id,title,content,note_type,is_pinned,created_at,updated_at,?,?,?,NULL FROM notes_v1""",
        (workspace_id, actor_id, actor_id),
    )
    connection.execute(
        """CREATE TABLE note_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT, note_id INTEGER NOT NULL, source_app TEXT NOT NULL,
        source_type TEXT NOT NULL, source_id TEXT NOT NULL, source_title_snapshot TEXT NOT NULL,
        created_at TEXT NOT NULL, workspace_id TEXT NOT NULL,
        FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE,
        UNIQUE(workspace_id,note_id,source_app,source_type,source_id))"""
    )
    connection.execute("INSERT INTO note_links SELECT id,note_id,source_app,source_type,source_id,source_title_snapshot,created_at,? FROM note_links_v1", (workspace_id,))
    connection.execute("DROP TABLE note_links_v1")
    connection.execute("DROP TABLE notes_v1")
    for table, value in sequences.items(): restore_sequence(connection, table, value)
    connection.execute("CREATE INDEX idx_notes_listing ON notes(is_pinned DESC, updated_at DESC, id DESC)")
    connection.execute("CREATE INDEX idx_notes_workspace_listing ON notes(workspace_id,is_pinned DESC,updated_at DESC,id DESC)")
    connection.execute("CREATE INDEX idx_notes_workspace_deleted ON notes(workspace_id,deleted_at,id)")
    connection.execute("CREATE INDEX idx_note_links_note ON note_links(note_id,id)")
    connection.execute("CREATE INDEX idx_note_links_source ON note_links(source_app,source_type,source_id,note_id)")
    connection.execute("CREATE INDEX idx_note_links_workspace_source ON note_links(workspace_id,source_app,source_type,source_id,note_id)")


def _verify(connection, context) -> None:
    workspace_id = required_uuid(context, "workspace_id")
    actor_id = required_uuid(context, "actor_id")
    expected = {"notes": {"workspace_id", "created_by", "updated_by"}, "note_links": {"workspace_id"}}
    for table, fields in expected.items():
        info = {str(row[1]): row for row in connection.execute(f"PRAGMA table_info({table})")}
        if not fields.issubset(info) or any(info[field][3] != 1 for field in fields):
            raise RuntimeError(f"Notes v2 {table} tenancy columns must be NOT NULL")
    assert_backfilled(connection, "notes", ("workspace_id", "created_by", "updated_by"), workspace_id, actor_id)
    assert_backfilled(connection, "note_links", ("workspace_id",), workspace_id)
    if ("workspace_id", "note_id", "source_app", "source_type", "source_id") not in index_columns(connection, "note_links"):
        raise RuntimeError("Notes v2 workspace link uniqueness is missing")
    note_indexes = all_index_columns(connection, "notes")
    if ("workspace_id", "is_pinned", "updated_at", "id") not in note_indexes or ("workspace_id", "deleted_at", "id") not in note_indexes:
        raise RuntimeError("Notes v2 workspace indexes are incomplete")
    if get_user_version(connection) != 2:
        raise RuntimeError("Notes migration did not reach v2")


MIGRATIONS = (MigrationStep(1, 2, _apply, _verify),)
TARGET_VERSION = 2
