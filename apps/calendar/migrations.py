"""Offline Calendar v2 -> v3 workspace migration (staging copies only)."""

from __future__ import annotations

from shared.migrations import MigrationStep, get_user_version
from shared.migrations.domain_context import assert_backfilled, index_columns, required_uuid, restore_sequence, sequence_value


def _apply(connection, context) -> None:
    workspace_id = required_uuid(context, "workspace_id")
    actor_id = required_uuid(context, "actor_id")
    sequences = {table: sequence_value(connection, table) for table in ("tasks", "topics")}
    connection.execute("ALTER TABLE task_topics RENAME TO task_topics_v2")
    connection.execute("ALTER TABLE topics RENAME TO topics_v2")
    connection.execute("ALTER TABLE tasks RENAME TO tasks_v2")
    connection.execute(
        """CREATE TABLE tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo','doing','done')),
        position INTEGER NOT NULL CHECK(position >= 0), all_day INTEGER NOT NULL DEFAULT 0 CHECK(all_day IN (0,1)),
        start_at TEXT NOT NULL CHECK(length(start_at)=25 AND substr(start_at,20,6)='+09:00'),
        due_at TEXT NOT NULL CHECK(length(due_at)=25 AND substr(due_at,20,6)='+09:00' AND start_at<=due_at),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, workspace_id TEXT NOT NULL,
        created_by TEXT NOT NULL, updated_by TEXT NOT NULL, deleted_at TEXT)"""
    )
    connection.execute(
        "INSERT INTO tasks SELECT id,title,description,status,position,all_day,start_at,due_at,created_at,updated_at,?,?,?,NULL FROM tasks_v2",
        (workspace_id, actor_id, actor_id),
    )
    connection.execute(
        """CREATE TABLE topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL COLLATE NOCASE,
        color TEXT NOT NULL CHECK(color GLOB '#[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]'),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, workspace_id TEXT NOT NULL,
        UNIQUE(workspace_id,name))"""
    )
    connection.execute("INSERT INTO topics SELECT id,name,color,created_at,updated_at,? FROM topics_v2", (workspace_id,))
    connection.execute(
        """CREATE TABLE task_topics (
        task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        topic_id INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
        workspace_id TEXT NOT NULL, PRIMARY KEY(workspace_id,task_id,topic_id))"""
    )
    connection.execute("INSERT INTO task_topics SELECT task_id,topic_id,? FROM task_topics_v2", (workspace_id,))
    connection.execute("DROP TABLE task_topics_v2")
    connection.execute("DROP TABLE topics_v2")
    connection.execute("DROP TABLE tasks_v2")
    for table, value in sequences.items(): restore_sequence(connection, table, value)
    connection.execute("CREATE INDEX idx_tasks_status_position ON tasks(status,position)")
    connection.execute("CREATE INDEX idx_tasks_date_range ON tasks(start_at,due_at)")
    connection.execute("CREATE INDEX idx_task_topics_topic_task ON task_topics(topic_id,task_id)")
    connection.execute("CREATE INDEX idx_tasks_workspace_status ON tasks(workspace_id,deleted_at,status,position)")
    connection.execute("CREATE INDEX idx_tasks_workspace_dates ON tasks(workspace_id,deleted_at,start_at,due_at)")
    connection.execute("CREATE INDEX idx_topics_workspace_listing ON topics(workspace_id,name,id)")


def _verify(connection, context) -> None:
    workspace_id = required_uuid(context, "workspace_id")
    actor_id = required_uuid(context, "actor_id")
    expected = {
        "tasks": {"workspace_id", "created_by", "updated_by"},
        "topics": {"workspace_id"},
        "task_topics": {"workspace_id"},
    }
    for table, fields in expected.items():
        info = {str(row[1]): row for row in connection.execute(f"PRAGMA table_info({table})")}
        if not fields.issubset(info) or any(info[field][3] != 1 for field in fields):
            raise RuntimeError(f"Calendar v3 {table} tenancy columns must be NOT NULL")
    assert_backfilled(connection, "tasks", ("workspace_id", "created_by", "updated_by"), workspace_id, actor_id)
    assert_backfilled(connection, "topics", ("workspace_id",), workspace_id)
    assert_backfilled(connection, "task_topics", ("workspace_id",), workspace_id)
    if ("workspace_id", "name") not in index_columns(connection, "topics"):
        raise RuntimeError("Calendar v3 workspace topic uniqueness is missing")
    if get_user_version(connection) != 3:
        raise RuntimeError("Calendar migration did not reach v3")


MIGRATIONS = (MigrationStep(2, 3, _apply, _verify),)
TARGET_VERSION = 3
