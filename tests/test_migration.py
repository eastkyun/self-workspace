import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.main import initialize_database


def create_legacy_database(path: Path, rows: list[tuple] | None = None) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'todo',
                position INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX idx_tasks_status_position
            ON tasks (status, position);
            """
        )
        connection.executemany(
            """
            INSERT INTO tasks (
                id, title, description, status, position, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows or [],
        )


def test_migrates_legacy_rows_and_creates_backup(tmp_path: Path):
    database_path = tmp_path / "tasks.db"
    create_legacy_database(
        database_path,
        [
            (
                7,
                "기존 작업",
                "보존할 설명",
                "doing",
                3,
                "2026-07-28T00:30:15+00:00",
                "2026-07-28T01:00:00+00:00",
            )
        ],
    )

    initialize_database(database_path)

    backup_path = Path(f"{database_path}.pre-calendar-v0.bak")
    assert backup_path.exists()
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute("SELECT * FROM tasks").fetchone()
        assert dict(row) == {
            "id": 7,
            "title": "기존 작업",
            "description": "보존할 설명",
            "status": "doing",
            "position": 3,
            "all_day": 0,
            "start_at": "2026-07-28T09:30:15+09:00",
            "due_at": "2026-07-28T10:30:15+09:00",
            "created_at": "2026-07-28T00:30:15+00:00",
            "updated_at": "2026-07-28T01:00:00+00:00",
        }
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        indexes = {
            item[1] for item in connection.execute("PRAGMA index_list(tasks)")
        }
        assert {"idx_tasks_status_position", "idx_tasks_date_range"} <= indexes

    with sqlite3.connect(backup_path) as backup:
        columns = {
            row[1] for row in backup.execute("PRAGMA table_info(tasks)")
        }
        assert "start_at" not in columns


def test_migration_falls_back_to_updated_at(tmp_path: Path):
    database_path = tmp_path / "tasks.db"
    create_legacy_database(
        database_path,
        [(1, "작업", "", "todo", 0, "invalid", "2026-07-28T12:00:00+09:00")],
    )

    initialize_database(database_path, create_backup=False)

    with sqlite3.connect(database_path) as connection:
        row = connection.execute(
            "SELECT start_at, due_at FROM tasks"
        ).fetchone()
    assert row == (
        "2026-07-28T12:00:00+09:00",
        "2026-07-28T13:00:00+09:00",
    )


def test_invalid_legacy_timestamps_roll_back_without_data_loss(tmp_path: Path):
    database_path = tmp_path / "tasks.db"
    create_legacy_database(
        database_path,
        [(1, "작업", "", "todo", 0, "invalid", "also-invalid")],
    )

    with pytest.raises(RuntimeError, match="backup:.*no usable timestamp"):
        initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)")
        }
        row = connection.execute("SELECT title FROM tasks").fetchone()
    assert "start_at" not in columns
    assert row == ("작업",)


def test_migration_is_idempotent_and_preserves_next_id(tmp_path: Path):
    database_path = tmp_path / "tasks.db"
    create_legacy_database(
        database_path,
        [(42, "작업", "", "todo", 0, "2026-07-28T00:00:00Z", "invalid")],
    )

    initialize_database(database_path)
    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO tasks (
                title, description, status, position, all_day,
                start_at, due_at, created_at, updated_at
            )
            VALUES ('새 작업', '', 'todo', 1, 0, ?, ?, ?, ?)
            """,
            (
                "2026-07-28T09:00:00+09:00",
                "2026-07-28T10:00:00+09:00",
                "2026-07-28T00:00:00Z",
                "2026-07-28T00:00:00Z",
            ),
        )
    assert cursor.lastrowid == 43


def test_concurrent_initialization_migrates_legacy_database_once(tmp_path: Path):
    database_path = tmp_path / "tasks.db"
    create_legacy_database(
        database_path,
        [(9, "동시 시작", "", "doing", 0, "2026-07-28T00:00:00Z", "invalid")],
    )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: initialize_database(database_path), range(2)))

    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT id, all_day, start_at, due_at FROM tasks"
        ).fetchall()
        version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert rows == [
        (
            9,
            0,
            "2026-07-28T09:00:00+09:00",
            "2026-07-28T10:00:00+09:00",
        )
    ]
    assert version == 1


def test_new_database_uses_schema_v1(tmp_path: Path):
    database_path = tmp_path / "tasks.db"

    initialize_database(database_path)

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)")
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert {"all_day", "start_at", "due_at"} <= columns
    assert version == 1
