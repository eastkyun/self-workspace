from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.app_registry import AppRegistry, PORTAL_STATIC_DIR
from shared.templating import SHARED_STATIC_DIR, create_templates, shell_context

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"
WORKSPACE_ROOT = APP_DIR.parents[2]
DEFAULT_DB_PATH = WORKSPACE_ROOT / "instance" / "calendar" / "tasks.db"
TaskStatus = Literal["todo", "doing", "done"]
KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = 2
CALENDAR_SCHEMA_VERSION = 1
templates = create_templates(TEMPLATES_DIR)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def canonical_kst(value: datetime) -> str:
    return value.astimezone(KST).isoformat(timespec="seconds")


def normalize_schedule(
    start_at: datetime, due_at: datetime, all_day: bool
) -> tuple[str, str]:
    start_kst = start_at.astimezone(KST).replace(microsecond=0)
    due_kst = due_at.astimezone(KST).replace(microsecond=0)
    if all_day:
        start_kst = datetime.combine(start_kst.date(), time.min, KST)
        due_kst = datetime.combine(due_kst.date(), time(23, 59, 59), KST)
    if start_kst > due_kst:
        raise ValueError("start_at must be before or equal to due_at")
    return canonical_kst(start_kst), canonical_kst(due_kst)


def parse_aware_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"invalid ISO timestamp: {value!r}") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timezone-aware timestamp required: {value!r}")
    return parsed


class ScheduleFields(BaseModel):
    @field_validator("start_at", "due_at", check_fields=False)
    @classmethod
    def timestamp_must_be_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timezone-aware timestamp required")
        return value


class TaskCreate(ScheduleFields):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)
    description: str = ""
    status: TaskStatus = "todo"
    all_day: bool = False
    start_at: datetime
    due_at: datetime
    topic_ids: list[int] = Field(default_factory=list)

    @field_validator("topic_ids")
    @classmethod
    def topic_ids_must_be_positive_and_unique(cls, value: list[int]) -> list[int]:
        if any(topic_id <= 0 for topic_id in value):
            raise ValueError("topic IDs must be positive")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def schedule_must_be_ordered(self) -> TaskCreate:
        normalize_schedule(self.start_at, self.due_at, self.all_day)
        return self


class TaskUpdate(ScheduleFields):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    status: TaskStatus | None = None
    position: int | None = Field(default=None, ge=0)
    all_day: bool | None = None
    start_at: datetime | None = None
    due_at: datetime | None = None
    topic_ids: list[int] | None = None

    @field_validator("topic_ids")
    @classmethod
    def topic_ids_must_be_positive_and_unique(
        cls, value: list[int] | None
    ) -> list[int] | None:
        if value is not None and any(topic_id <= 0 for topic_id in value):
            raise ValueError("topic IDs must be positive")
        return None if value is None else list(dict.fromkeys(value))

    @model_validator(mode="after")
    def supplied_schedule_must_be_ordered(self) -> TaskUpdate:
        if self.start_at is not None and self.due_at is not None:
            normalize_schedule(self.start_at, self.due_at, bool(self.all_day))
        return self


class TopicFields(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=40)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator("color")
    @classmethod
    def normalize_color(cls, value: str) -> str:
        return value.upper()


class TopicCreate(TopicFields):
    pass


class TopicUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str | None = Field(default=None, min_length=1, max_length=40)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator("color")
    @classmethod
    def normalize_color(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


class Topic(BaseModel):
    id: int
    name: str
    color: str
    created_at: str
    updated_at: str


class Task(BaseModel):
    id: int
    title: str
    description: str
    status: TaskStatus
    position: int
    all_day: bool
    start_at: str
    due_at: str
    created_at: str
    updated_at: str
    topics: list[Topic] = Field(default_factory=list)


def row_to_topic(row: sqlite3.Row) -> Topic:
    return Topic.model_validate(
        {field: row[field] for field in Topic.model_fields}
    )


def row_to_task(row: sqlite3.Row, topics: list[Topic] | None = None) -> Task:
    return Task.model_validate({**dict(row), "topics": topics or []})


def resolve_database_path(database_path: str | Path | None) -> Path:
    configured = (
        database_path
        or os.getenv("CALENDAR_DB_PATH")
        or os.getenv("TODO_DB_PATH")
        or DEFAULT_DB_PATH
    )
    path = Path(configured).expanduser()
    return path if path.is_absolute() else WORKSPACE_ROOT / path


def create_tasks_schema(connection: sqlite3.Connection, table: str = "tasks") -> None:
    connection.execute(
        f"""
        CREATE TABLE {table} (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo'
                CHECK (status IN ('todo', 'doing', 'done')),
            position INTEGER NOT NULL CHECK (position >= 0),
            all_day INTEGER NOT NULL DEFAULT 0
                CHECK (all_day IN (0, 1)),
            start_at TEXT NOT NULL
                CHECK (length(start_at) = 25 AND substr(start_at, 20, 6) = '+09:00'),
            due_at TEXT NOT NULL
                CHECK (
                    length(due_at) = 25
                    AND substr(due_at, 20, 6) = '+09:00'
                    AND start_at <= due_at
                ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def create_topics_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            color TEXT NOT NULL CHECK (
                color GLOB '#[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]'
                    || '[0-9A-Fa-f][0-9A-Fa-f][0-9A-Fa-f]'
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE task_topics (
            task_id INTEGER NOT NULL
                REFERENCES tasks(id) ON DELETE CASCADE,
            topic_id INTEGER NOT NULL
                REFERENCES topics(id) ON DELETE CASCADE,
            PRIMARY KEY (task_id, topic_id)
        )
        """
    )


def create_latest_schema(connection: sqlite3.Connection) -> None:
    create_tasks_schema(connection)
    create_topics_schema(connection)


def create_indexes(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_status_position "
        "ON tasks (status, position)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_tasks_date_range "
        "ON tasks (start_at, due_at)"
    )
    if connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'task_topics'"
    ).fetchone():
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_task_topics_topic_task "
            "ON task_topics (topic_id, task_id)"
        )


def backup_database(database_path: Path, suffix: str) -> Path | None:
    if str(database_path) == ":memory:":
        return None
    backup_path = Path(f"{database_path}.{suffix}")
    if not backup_path.exists():
        with (
            sqlite3.connect(database_path) as source,
            sqlite3.connect(backup_path) as backup,
        ):
            source.backup(backup)
    return backup_path


def migrate_v0_to_v1(
    connection: sqlite3.Connection, database_path: Path, create_backup: bool
) -> None:
    backup_path = (
        backup_database(database_path, "pre-calendar-v0.bak")
        if create_backup
        else None
    )
    backup_note = f"; backup: {backup_path}" if backup_path else ""
    try:
        create_tasks_schema(connection, "tasks_v1")
        legacy_rows = connection.execute(
            """
            SELECT id, title, description, status, position, created_at, updated_at
            FROM tasks
            ORDER BY id
            """
        ).fetchall()
        legacy_ids = {row["id"] for row in legacy_rows}
        for row in legacy_rows:
            schedule_source = None
            errors: list[str] = []
            for field in ("created_at", "updated_at"):
                try:
                    schedule_source = parse_aware_timestamp(row[field])
                    break
                except ValueError as error:
                    errors.append(str(error))
            if schedule_source is None:
                raise RuntimeError(
                    f"task {row['id']} has no usable timestamp: {', '.join(errors)}"
                )
            start_at = canonical_kst(schedule_source)
            due_at = canonical_kst(schedule_source + timedelta(hours=1))
            connection.execute(
                """
                INSERT INTO tasks_v1 (
                    id, title, description, status, position, all_day,
                    start_at, due_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["title"],
                    row["description"],
                    row["status"],
                    row["position"],
                    start_at,
                    due_at,
                    row["created_at"],
                    row["updated_at"],
                ),
            )

        migrated_ids = {
            row[0] for row in connection.execute("SELECT id FROM tasks_v1")
        }
        if migrated_ids != legacy_ids:
            raise RuntimeError("migrated task IDs do not match legacy task IDs")

        connection.execute("DROP TABLE tasks")
        connection.execute("ALTER TABLE tasks_v1 RENAME TO tasks")
        create_indexes(connection)
        connection.execute(f"PRAGMA user_version = {CALENDAR_SCHEMA_VERSION}")
    except Exception as error:
        raise RuntimeError(f"calendar schema migration failed{backup_note}: {error}") from error


def migrate_v1_to_v2(
    connection: sqlite3.Connection,
    database_path: Path,
    create_backup: bool,
) -> None:
    backup_path = (
        backup_database(database_path, "pre-topics-v1.bak")
        if create_backup
        else None
    )
    backup_note = f"; backup: {backup_path}" if backup_path else ""
    try:
        create_topics_schema(connection)
        create_indexes(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    except Exception as error:
        raise RuntimeError(f"topics schema migration failed{backup_note}: {error}") from error


def initialize_database(database_path: Path, *, create_backup: bool = True) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(database_path, isolation_level=None) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            if "tasks" not in tables:
                if {"topics", "task_topics"}.intersection(tables):
                    raise RuntimeError("database has a partial topics schema")
                create_latest_schema(connection)
                create_indexes(connection)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            else:
                columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(tasks)")
                }
                schedule_columns = {"all_day", "start_at", "due_at"}
                if not schedule_columns.intersection(columns):
                    migrate_v0_to_v1(connection, database_path, create_backup)
                    migrate_v1_to_v2(connection, database_path, create_backup=False)
                elif not schedule_columns.issubset(columns):
                    raise RuntimeError(
                        "tasks table has a partially migrated calendar schema"
                    )
                else:
                    topic_tables = {"topics", "task_topics"}.intersection(tables)
                    if not topic_tables:
                        migrate_v1_to_v2(
                            connection, database_path, create_backup=create_backup
                        )
                    elif topic_tables != {"topics", "task_topics"}:
                        raise RuntimeError("database has a partial topics schema")
                    else:
                        topic_columns = {
                            row["name"]
                            for row in connection.execute("PRAGMA table_info(topics)")
                        }
                        relation_columns = {
                            row["name"]
                            for row in connection.execute(
                                "PRAGMA table_info(task_topics)"
                            )
                        }
                        if not {"id", "name", "color"}.issubset(topic_columns) or not {
                            "task_id",
                            "topic_id",
                        }.issubset(relation_columns):
                            raise RuntimeError("database has a partial topics schema")
                        create_indexes(connection)
                        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def verify_database(database_path: Path) -> None:
    if not database_path.is_file():
        raise RuntimeError(f"calendar database does not exist: {database_path}")

    database_uri = f"{database_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")

        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"calendar database quick_check failed: {quick_check}")

        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError("calendar database foreign_key_check failed")

        user_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if user_version != SCHEMA_VERSION:
            raise RuntimeError(
                "calendar database requires an offline migration: "
                f"expected schema v{SCHEMA_VERSION}, found v{user_version}"
            )

        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        required_tables = {"tasks", "topics", "task_topics"}
        if not required_tables.issubset(tables):
            missing = ", ".join(sorted(required_tables - tables))
            raise RuntimeError(f"calendar database is missing tables: {missing}")

        required_columns = {
            "tasks": {
                "id",
                "title",
                "description",
                "status",
                "position",
                "all_day",
                "start_at",
                "due_at",
                "created_at",
                "updated_at",
            },
            "topics": {"id", "name", "color", "created_at", "updated_at"},
            "task_topics": {"task_id", "topic_id"},
        }
        for table, expected_columns in required_columns.items():
            actual_columns = {
                row["name"]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            missing_columns = expected_columns - actual_columns
            if missing_columns:
                missing = ", ".join(sorted(missing_columns))
                raise RuntimeError(
                    f"calendar database table {table} is missing columns: {missing}"
                )


@contextmanager
def database_connection(database_path: Path) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
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


def fetch_topic(connection: sqlite3.Connection, topic_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Topic not found")
    return row


def task_topics(
    connection: sqlite3.Connection, task_ids: list[int]
) -> dict[int, list[Topic]]:
    result = {task_id: [] for task_id in task_ids}
    if not task_ids:
        return result
    placeholders = ", ".join("?" for _ in task_ids)
    rows = connection.execute(
        f"""
        SELECT tt.task_id, t.id, t.name, t.color, t.created_at, t.updated_at
        FROM task_topics AS tt
        JOIN topics AS t ON t.id = tt.topic_id
        WHERE tt.task_id IN ({placeholders})
        ORDER BY t.name COLLATE NOCASE, t.id
        """,
        task_ids,
    ).fetchall()
    for row in rows:
        result[row["task_id"]].append(row_to_topic(row))
    return result


def rows_to_tasks(
    connection: sqlite3.Connection, rows: list[sqlite3.Row]
) -> list[Task]:
    topics_by_task = task_topics(connection, [row["id"] for row in rows])
    return [
        row_to_task(row, topics_by_task.get(row["id"], []))
        for row in rows
    ]


def task_from_row(connection: sqlite3.Connection, row: sqlite3.Row) -> Task:
    return rows_to_tasks(connection, [row])[0]


def validate_topic_ids(
    connection: sqlite3.Connection, topic_ids: list[int]
) -> None:
    if not topic_ids:
        return
    placeholders = ", ".join("?" for _ in topic_ids)
    found = {
        row["id"]
        for row in connection.execute(
            f"SELECT id FROM topics WHERE id IN ({placeholders})", topic_ids
        )
    }
    missing = [topic_id for topic_id in topic_ids if topic_id not in found]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown topic IDs: {', '.join(map(str, missing))}",
        )


def replace_task_topics(
    connection: sqlite3.Connection, task_id: int, topic_ids: list[int]
) -> None:
    connection.execute("DELETE FROM task_topics WHERE task_id = ?", (task_id,))
    connection.executemany(
        "INSERT INTO task_topics (task_id, topic_id) VALUES (?, ?)",
        [(task_id, topic_id) for topic_id in topic_ids],
    )


def ensure_topic_name_available(
    connection: sqlite3.Connection,
    name: str,
    *,
    excluded_id: int | None = None,
) -> None:
    query = "SELECT 1 FROM topics WHERE name = ? COLLATE NOCASE"
    values: list[object] = [name]
    if excluded_id is not None:
        query += " AND id != ?"
        values.append(excluded_id)
    if connection.execute(query, values).fetchone():
        raise HTTPException(status_code=409, detail="Topic name already exists")


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


def create_app(
    database_path: str | Path | None = None,
    *,
    initialize_on_startup: bool = True,
    app_registry: AppRegistry | None = None,
) -> FastAPI:
    resolved_database_path = resolve_database_path(database_path)
    registry = app_registry or AppRegistry.load_default()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if initialize_on_startup:
            initialize_database(resolved_database_path)
        yield

    application = FastAPI(
        title="Calendar Diary",
        version="2.0.0",
        lifespan=lifespan,
    )
    application.state.database_path = resolved_database_path
    application.state.app_registry = registry
    application.mount(
        "/static/icons",
        StaticFiles(directory=PORTAL_STATIC_DIR / "icons"),
        name="workspace-icons",
    )
    application.mount(
        "/static/shared",
        StaticFiles(directory=SHARED_STATIC_DIR),
        name="shared-static",
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @application.get("/", include_in_schema=False)
    def index(request: Request):
        return templates.TemplateResponse(
            request,
            "calendar.html",
            shell_context(
                request,
                registry,
                current_app_id="calendar",
                page_title="Calendar",
            ),
        )

    @application.get("/api/topics", response_model=list[Topic])
    def list_topics() -> list[Topic]:
        with database_connection(resolved_database_path) as connection:
            rows = connection.execute(
                "SELECT * FROM topics ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        return [row_to_topic(row) for row in rows]

    @application.post(
        "/api/topics", response_model=Topic, status_code=status.HTTP_201_CREATED
    )
    def create_topic(payload: TopicCreate) -> Topic:
        now = utc_now()
        with database_connection(resolved_database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            ensure_topic_name_available(connection, payload.name)
            cursor = connection.execute(
                """
                INSERT INTO topics (name, color, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (payload.name, payload.color, now, now),
            )
            row = fetch_topic(connection, cursor.lastrowid)
        return row_to_topic(row)

    @application.patch("/api/topics/{topic_id}", response_model=Topic)
    def update_topic(topic_id: int, payload: TopicUpdate) -> Topic:
        changes = payload.model_dump(exclude_unset=True)
        if not changes or any(value is None for value in changes.values()):
            raise HTTPException(
                status_code=422,
                detail="At least one non-null topic field is required",
            )
        with database_connection(resolved_database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            fetch_topic(connection, topic_id)
            if "name" in changes:
                ensure_topic_name_available(
                    connection, changes["name"], excluded_id=topic_id
                )
            assignments = [f"{field} = ?" for field in changes]
            values = list(changes.values())
            assignments.append("updated_at = ?")
            values.extend((utc_now(), topic_id))
            connection.execute(
                f"UPDATE topics SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            row = fetch_topic(connection, topic_id)
        return row_to_topic(row)

    @application.delete(
        "/api/topics/{topic_id}", status_code=status.HTTP_204_NO_CONTENT
    )
    def delete_topic(topic_id: int) -> Response:
        with database_connection(resolved_database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            fetch_topic(connection, topic_id)
            connection.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @application.get("/api/tasks", response_model=list[Task])
    def list_tasks() -> list[Task]:
        with database_connection(resolved_database_path) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM tasks
                ORDER BY start_at, due_at, position, id
                """
            ).fetchall()
            return rows_to_tasks(connection, rows)

    @application.post(
        "/api/tasks", response_model=Task, status_code=status.HTTP_201_CREATED
    )
    def create_task(payload: TaskCreate) -> Task:
        now = utc_now()
        start_at, due_at = normalize_schedule(
            payload.start_at, payload.due_at, payload.all_day
        )
        with database_connection(resolved_database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            validate_topic_ids(connection, payload.topic_ids)
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
                    title, description, status, position, all_day,
                    start_at, due_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.title,
                    payload.description,
                    payload.status,
                    next_position,
                    payload.all_day,
                    start_at,
                    due_at,
                    now,
                    now,
                ),
            )
            row = fetch_task(connection, cursor.lastrowid)
            replace_task_topics(
                connection, cursor.lastrowid, payload.topic_ids
            )
            result = task_from_row(connection, row)
        return result

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
            if "topic_ids" in changes:
                validate_topic_ids(connection, changes["topic_ids"])
            old_status = current["status"]
            target_status = changes.get("status", old_status)

            if "status" in changes or "position" in changes:
                target_ids = ordered_task_ids(
                    connection, target_status, excluded_id=task_id
                )
                requested_position = changes.get("position")
                if requested_position is None:
                    requested_position = (
                        current["position"]
                        if target_status == old_status
                        else len(target_ids)
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

            if {"all_day", "start_at", "due_at"}.intersection(changes):
                start_value = changes.get(
                    "start_at", parse_aware_timestamp(current["start_at"])
                )
                due_value = changes.get(
                    "due_at", parse_aware_timestamp(current["due_at"])
                )
                all_day = changes.get("all_day", bool(current["all_day"]))
                try:
                    start_at, due_at = normalize_schedule(
                        start_value, due_value, all_day
                    )
                except ValueError as error:
                    raise HTTPException(status_code=422, detail=str(error)) from error
                assignments.extend(("all_day = ?", "start_at = ?", "due_at = ?"))
                values.extend((all_day, start_at, due_at))

            assignments.append("updated_at = ?")
            values.append(utc_now())
            values.append(task_id)
            connection.execute(
                f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?", values
            )
            if "topic_ids" in changes:
                replace_task_topics(connection, task_id, changes["topic_ids"])
            row = fetch_task(connection, task_id)
            result = task_from_row(connection, row)
        return result

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
