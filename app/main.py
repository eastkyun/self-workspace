from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DEFAULT_DB_PATH = APP_DIR.parent / "instance" / "tasks.db"
TaskStatus = Literal["todo", "doing", "done"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class TaskCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    description: str = ""
    status: TaskStatus = "todo"


class TaskUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    status: TaskStatus | None = None
    position: int | None = Field(default=None, ge=0)


class Task(BaseModel):
    id: int
    title: str
    description: str
    status: TaskStatus
    position: int
    created_at: str
    updated_at: str


def row_to_task(row: sqlite3.Row) -> Task:
    return Task.model_validate(dict(row))


def resolve_database_path(database_path: str | Path | None) -> Path:
    configured = database_path or os.getenv("TODO_DB_PATH") or DEFAULT_DB_PATH
    return Path(configured)


def initialize_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'todo'
                    CHECK (status IN ('todo', 'doing', 'done')),
                position INTEGER NOT NULL CHECK (position >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_status_position
            ON tasks (status, position)
            """
        )


@contextmanager
def database_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def fetch_task(connection: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return row


def ordered_task_ids(
    connection: sqlite3.Connection, task_status: str, excluded_id: int
) -> list[int]:
    return [
        row["id"]
        for row in connection.execute(
            """
            SELECT id
            FROM tasks
            WHERE status = ? AND id != ?
            ORDER BY position, id
            """,
            (task_status, excluded_id),
        )
    ]


def write_positions(
    connection: sqlite3.Connection, task_status: str, task_ids: list[int]
) -> None:
    connection.executemany(
        "UPDATE tasks SET status = ?, position = ? WHERE id = ?",
        [(task_status, position, task_id) for position, task_id in enumerate(task_ids)],
    )


def create_app(database_path: str | Path | None = None) -> FastAPI:
    resolved_database_path = resolve_database_path(database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        initialize_database(resolved_database_path)
        yield

    application = FastAPI(
        title="Simple Kanban",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.database_path = resolved_database_path
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/api/tasks", response_model=list[Task])
    def list_tasks() -> list[Task]:
        with database_connection(resolved_database_path) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM tasks
                ORDER BY
                    CASE status
                        WHEN 'todo' THEN 0
                        WHEN 'doing' THEN 1
                        ELSE 2
                    END,
                    position,
                    id
                """
            ).fetchall()
        return [row_to_task(row) for row in rows]

    @application.post(
        "/api/tasks", response_model=Task, status_code=status.HTTP_201_CREATED
    )
    def create_task(payload: TaskCreate) -> Task:
        now = utc_now()
        with database_connection(resolved_database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            next_position = connection.execute(
                """
                SELECT COALESCE(MAX(position) + 1, 0)
                FROM tasks
                WHERE status = ?
                """,
                (payload.status,),
            ).fetchone()[0]
            cursor = connection.execute(
                """
                INSERT INTO tasks (
                    title, description, status, position, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.title,
                    payload.description,
                    payload.status,
                    next_position,
                    now,
                    now,
                ),
            )
            row = fetch_task(connection, cursor.lastrowid)
        return row_to_task(row)

    @application.patch("/api/tasks/{task_id}", response_model=Task)
    def update_task(task_id: int, payload: TaskUpdate) -> Task:
        changes = payload.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise HTTPException(
                status_code=422, detail="Updated fields cannot be null"
            )

        with database_connection(resolved_database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = fetch_task(connection, task_id)
            old_status = current["status"]
            target_status = changes.get("status", old_status)

            if "status" in changes or "position" in changes:
                target_ids = ordered_task_ids(
                    connection, target_status, excluded_id=task_id
                )
                requested_position = changes.get("position")
                if requested_position is None:
                    requested_position = (
                        current["position"] if target_status == old_status else len(target_ids)
                    )
                target_position = min(requested_position, len(target_ids))
                target_ids.insert(target_position, task_id)

                if old_status != target_status:
                    source_ids = ordered_task_ids(
                        connection, old_status, excluded_id=task_id
                    )
                    write_positions(connection, old_status, source_ids)
                write_positions(connection, target_status, target_ids)

            assignments: list[str] = []
            values: list[object] = []
            for field in ("title", "description"):
                if field in changes:
                    assignments.append(f"{field} = ?")
                    values.append(changes[field])

            assignments.append("updated_at = ?")
            values.append(utc_now())
            values.append(task_id)
            connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?", values
            )
            row = fetch_task(connection, task_id)
        return row_to_task(row)

    @application.delete(
        "/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    def delete_task(task_id: int) -> Response:
        with database_connection(resolved_database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = fetch_task(connection, task_id)
            connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            remaining_ids = ordered_task_ids(
                connection, current["status"], excluded_id=task_id
            )
            write_positions(connection, current["status"], remaining_ids)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return application


app = create_app()
