from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest

from apps.calendar.app import repository
from apps.calendar.app.main import initialize_database
from apps.calendar.migrations import MIGRATIONS, TARGET_VERSION
from shared.migrations import migrate


@pytest.fixture
def tenant_database(tmp_path):
    path = tmp_path / "calendar.db"
    initialize_database(path, create_backup=False)
    workspace_a, workspace_b, actor_a, actor_b = (
        str(uuid4()) for _ in range(4)
    )
    with sqlite3.connect(path) as connection:
        migrate(
            connection,
            target_version=TARGET_VERSION,
            migrations=MIGRATIONS,
            context={"workspace_id": workspace_a, "actor_id": actor_a},
        )
        connection.execute(
            "INSERT INTO topics(name,color,created_at,updated_at,workspace_id) VALUES('A','#112233','c','u',?)",
            (workspace_a,),
        )
        topic_a = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            "INSERT INTO topics(name,color,created_at,updated_at,workspace_id) VALUES('B','#445566','c','u',?)",
            (workspace_b,),
        )
        topic_b = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    return path, workspace_a, workspace_b, actor_a, actor_b, topic_a, topic_b


def _create(connection, workspace_id: str, actor_id: str):
    return repository.create_task(
        connection,
        workspace_id,
        actor_id=actor_id,
        title="Private event",
        description="",
        status="todo",
        position=0,
        all_day=False,
        start_at="2026-08-06T09:00:00+09:00",
        due_at="2026-08-06T10:00:00+09:00",
        timestamp="2026-08-06T00:00:00+00:00",
    )


def test_task_crud_is_workspace_scoped_and_maintains_actor_fields(tenant_database):
    path, workspace_a, workspace_b, actor_a, actor_b, _, _ = tenant_database
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        task = _create(connection, workspace_a, actor_a)
        assert task["created_by"] == task["updated_by"] == actor_a
        assert repository.get_task(connection, workspace_b, task["id"]) is None
        assert repository.list_tasks(connection, workspace_b) == []
        assert repository.update_task(
            connection,
            workspace_b,
            task["id"],
            actor_id=actor_b,
            title="stolen",
            description="",
            status="todo",
            position=0,
            all_day=False,
            start_at=task["start_at"],
            due_at=task["due_at"],
            timestamp="2026-08-06T01:00:00+00:00",
        ) is None
        updated = repository.update_task(
            connection,
            workspace_a,
            task["id"],
            actor_id=actor_b,
            title="Updated",
            description="body",
            status="doing",
            position=0,
            all_day=False,
            start_at=task["start_at"],
            due_at=task["due_at"],
            timestamp="2026-08-06T01:00:00+00:00",
        )
        assert updated["created_by"] == actor_a
        assert updated["updated_by"] == actor_b


def test_soft_delete_restore_and_topics_cannot_cross_workspace(tenant_database):
    path, workspace_a, workspace_b, actor_a, actor_b, topic_a, topic_b = tenant_database
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        task = _create(connection, workspace_a, actor_a)
        assert repository.replace_task_topics(connection, workspace_a, task["id"], [topic_b]) is False
        assert repository.list_task_topics(connection, workspace_a, task["id"]) == []
        assert repository.replace_task_topics(connection, workspace_a, task["id"], [topic_a]) is True
        assert [topic["id"] for topic in repository.list_task_topics(connection, workspace_a, task["id"])] == [topic_a]
        assert repository.list_task_topics(connection, workspace_b, task["id"]) == []

        assert repository.soft_delete_task(
            connection, workspace_b, task["id"], actor_id=actor_b, timestamp="deleted"
        ) is False
        assert repository.soft_delete_task(
            connection, workspace_a, task["id"], actor_id=actor_a, timestamp="deleted"
        ) is True
        assert repository.get_task(connection, workspace_a, task["id"]) is None
        assert repository.list_task_topics(connection, workspace_a, task["id"]) == []
        restored = repository.restore_task(
            connection, workspace_a, task["id"], actor_id=actor_b, timestamp="restored"
        )
        assert restored["updated_by"] == actor_b
