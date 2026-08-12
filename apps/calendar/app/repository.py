from __future__ import annotations

import sqlite3
from typing import Any


def _dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def get_task(connection: sqlite3.Connection, workspace_id: str, task_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM tasks WHERE workspace_id=? AND id=? AND deleted_at IS NULL",
        (workspace_id, task_id),
    ).fetchone()
    return _dict(row) if row else None


def get_deleted_task(connection: sqlite3.Connection, workspace_id: str, task_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM tasks WHERE workspace_id=? AND id=? AND deleted_at IS NOT NULL",
        (workspace_id, task_id),
    ).fetchone()
    return _dict(row) if row else None


def list_tasks(connection: sqlite3.Connection, workspace_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT * FROM tasks WHERE workspace_id=? AND deleted_at IS NULL
        ORDER BY start_at, due_at, position, id""",
        (workspace_id,),
    ).fetchall()
    return [_dict(row) for row in rows]


def next_position(connection: sqlite3.Connection, workspace_id: str, status: str) -> int:
    return int(
        connection.execute(
            """SELECT COALESCE(MAX(position)+1, 0) FROM tasks
            WHERE workspace_id=? AND status=? AND deleted_at IS NULL""",
            (workspace_id, status),
        ).fetchone()[0]
    )


def ordered_task_ids(
    connection: sqlite3.Connection,
    workspace_id: str,
    status: str,
    *,
    excluded_id: int,
) -> list[int]:
    return [
        int(row[0])
        for row in connection.execute(
            """SELECT id FROM tasks
            WHERE workspace_id=? AND status=? AND id!=? AND deleted_at IS NULL
            ORDER BY position,id""",
            (workspace_id, status, excluded_id),
        )
    ]


def write_positions(
    connection: sqlite3.Connection,
    workspace_id: str,
    status: str,
    task_ids: list[int],
) -> None:
    connection.executemany(
        """UPDATE tasks SET status=?, position=?
        WHERE workspace_id=? AND id=? AND deleted_at IS NULL""",
        [
            (status, position, workspace_id, task_id)
            for position, task_id in enumerate(task_ids)
        ],
    )


def create_task(
    connection: sqlite3.Connection,
    workspace_id: str,
    *,
    actor_id: str,
    title: str,
    description: str,
    status: str,
    position: int,
    all_day: bool,
    start_at: str,
    due_at: str,
    timestamp: str,
) -> dict[str, Any]:
    cursor = connection.execute(
        """INSERT INTO tasks (
            title, description, status, position, all_day, start_at, due_at,
            created_at, updated_at, workspace_id, created_by, updated_by, deleted_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
        (
            title, description, status, position, int(all_day), start_at, due_at,
            timestamp, timestamp, workspace_id, actor_id, actor_id,
        ),
    )
    return get_task(connection, workspace_id, cursor.lastrowid)  # type: ignore[return-value]


def update_task(
    connection: sqlite3.Connection,
    workspace_id: str,
    task_id: int,
    *,
    actor_id: str,
    title: str,
    description: str,
    status: str,
    position: int,
    all_day: bool,
    start_at: str,
    due_at: str,
    timestamp: str,
) -> dict[str, Any] | None:
    cursor = connection.execute(
        """UPDATE tasks SET title=?, description=?, status=?, position=?, all_day=?,
        start_at=?, due_at=?, updated_at=?, updated_by=?
        WHERE workspace_id=? AND id=? AND deleted_at IS NULL""",
        (
            title, description, status, position, int(all_day), start_at, due_at,
            timestamp, actor_id, workspace_id, task_id,
        ),
    )
    return get_task(connection, workspace_id, task_id) if cursor.rowcount else None


def soft_delete_task(
    connection: sqlite3.Connection,
    workspace_id: str,
    task_id: int,
    *,
    actor_id: str,
    timestamp: str,
) -> bool:
    cursor = connection.execute(
        """UPDATE tasks SET deleted_at=?, updated_at=?, updated_by=?
        WHERE workspace_id=? AND id=? AND deleted_at IS NULL""",
        (timestamp, timestamp, actor_id, workspace_id, task_id),
    )
    return cursor.rowcount > 0


def restore_task(
    connection: sqlite3.Connection,
    workspace_id: str,
    task_id: int,
    *,
    actor_id: str,
    timestamp: str,
) -> dict[str, Any] | None:
    cursor = connection.execute(
        """UPDATE tasks SET deleted_at=NULL, updated_at=?, updated_by=?
        WHERE workspace_id=? AND id=? AND deleted_at IS NOT NULL""",
        (timestamp, actor_id, workspace_id, task_id),
    )
    return get_task(connection, workspace_id, task_id) if cursor.rowcount else None


def get_topic(connection: sqlite3.Connection, workspace_id: str, topic_id: int) -> dict[str, Any] | None:
    row = connection.execute(
        "SELECT * FROM topics WHERE workspace_id=? AND id=?",
        (workspace_id, topic_id),
    ).fetchone()
    return _dict(row) if row else None


def list_topics(connection: sqlite3.Connection, workspace_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT * FROM topics WHERE workspace_id=? ORDER BY name COLLATE NOCASE, id",
        (workspace_id,),
    ).fetchall()
    return [_dict(row) for row in rows]


def create_topic(
    connection: sqlite3.Connection,
    workspace_id: str,
    *,
    name: str,
    color: str,
    timestamp: str,
) -> dict[str, Any]:
    cursor = connection.execute(
        """INSERT INTO topics(name,color,created_at,updated_at,workspace_id)
        VALUES(?,?,?,?,?)""",
        (name, color, timestamp, timestamp, workspace_id),
    )
    return get_topic(connection, workspace_id, cursor.lastrowid)  # type: ignore[return-value]


def update_topic(
    connection: sqlite3.Connection,
    workspace_id: str,
    topic_id: int,
    *,
    name: str,
    color: str,
    timestamp: str,
) -> dict[str, Any] | None:
    cursor = connection.execute(
        """UPDATE topics SET name=?,color=?,updated_at=?
        WHERE workspace_id=? AND id=?""",
        (name, color, timestamp, workspace_id, topic_id),
    )
    return get_topic(connection, workspace_id, topic_id) if cursor.rowcount else None


def delete_topic(
    connection: sqlite3.Connection, workspace_id: str, topic_id: int
) -> bool:
    cursor = connection.execute(
        "DELETE FROM topics WHERE workspace_id=? AND id=?",
        (workspace_id, topic_id),
    )
    return cursor.rowcount > 0


def replace_task_topics(
    connection: sqlite3.Connection,
    workspace_id: str,
    task_id: int,
    topic_ids: list[int],
) -> bool:
    if get_task(connection, workspace_id, task_id) is None:
        return False
    if topic_ids:
        placeholders = ", ".join("?" for _ in topic_ids)
        found = {
            int(row[0])
            for row in connection.execute(
                f"SELECT id FROM topics WHERE workspace_id=? AND id IN ({placeholders})",
                (workspace_id, *topic_ids),
            )
        }
        if found != set(topic_ids):
            return False
    connection.execute(
        "DELETE FROM task_topics WHERE workspace_id=? AND task_id=?",
        (workspace_id, task_id),
    )
    connection.executemany(
        "INSERT INTO task_topics(workspace_id,task_id,topic_id) VALUES(?,?,?)",
        [(workspace_id, task_id, topic_id) for topic_id in topic_ids],
    )
    return True


def list_task_topics(
    connection: sqlite3.Connection, workspace_id: str, task_id: int
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT topic.* FROM task_topics AS binding
        JOIN topics AS topic ON topic.workspace_id=binding.workspace_id
            AND topic.id=binding.topic_id
        JOIN tasks AS task ON task.workspace_id=binding.workspace_id
            AND task.id=binding.task_id
        WHERE binding.workspace_id=? AND binding.task_id=? AND task.deleted_at IS NULL
        ORDER BY topic.name COLLATE NOCASE, topic.id""",
        (workspace_id, task_id),
    ).fetchall()
    return [_dict(row) for row in rows]
