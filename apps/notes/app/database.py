from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

APP_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = APP_DIR.parents[2]
DEFAULT_DB_PATH = WORKSPACE_ROOT / "instance" / "notes" / "notes.db"
NOTES_SCHEMA_VERSION = 3

REQUIRED_COLUMNS = {
    "notes": {
        "id",
        "title",
        "content",
        "note_type",
        "is_pinned",
        "created_at",
        "updated_at",
        "workspace_id",
        "created_by",
        "updated_by",
        "deleted_at",
    },
    "note_links": {
        "id",
        "note_id",
        "source_app",
        "source_type",
        "source_id",
        "source_title_snapshot",
        "created_at",
        "workspace_id",
    },
}
REQUIRED_INDEXES = {
    "idx_notes_listing",
    "idx_notes_workspace_listing",
    "idx_notes_workspace_deleted",
    "idx_note_links_note",
    "idx_note_links_source",
    "idx_note_links_workspace_source",
}
IDEMPOTENCY_REQUIRED_COLUMNS = {
    "workspace_id", "key", "note_id", "created_at"
}
IDEMPOTENCY_REQUIRED_INDEXES = {
    "idx_notes_workspace_id",
    "idx_note_idempotency_note",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    note_type TEXT NOT NULL DEFAULT 'general',
    is_pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    deleted_at TEXT,
    CHECK(length(trim(title)) BETWEEN 1 AND 120),
    CHECK(length(content) <= 100000),
    CHECK(note_type IN ('general', 'meeting_minutes', 'research_note')),
    CHECK(is_pinned IN (0, 1))
);

CREATE TABLE IF NOT EXISTS note_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL,
    source_app TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_title_snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE,
    UNIQUE(workspace_id, note_id, source_app, source_type, source_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_notes_workspace_id
ON notes(workspace_id, id);

CREATE TABLE IF NOT EXISTS note_idempotency_keys (
    workspace_id TEXT NOT NULL,
    key TEXT NOT NULL,
    note_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, key),
    FOREIGN KEY(workspace_id, note_id)
        REFERENCES notes(workspace_id, id) ON DELETE CASCADE,
    CHECK(length(key) BETWEEN 1 AND 255)
);

CREATE INDEX IF NOT EXISTS idx_note_idempotency_note
ON note_idempotency_keys(workspace_id, note_id);

CREATE INDEX IF NOT EXISTS idx_notes_listing
ON notes(is_pinned DESC, updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_notes_workspace_listing
ON notes(workspace_id, is_pinned DESC, updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_notes_workspace_deleted
ON notes(workspace_id, deleted_at, id);

CREATE INDEX IF NOT EXISTS idx_note_links_note
ON note_links(note_id, id);

CREATE INDEX IF NOT EXISTS idx_note_links_source
ON note_links(source_app, source_type, source_id, note_id);

CREATE INDEX IF NOT EXISTS idx_note_links_workspace_source
ON note_links(workspace_id, source_app, source_type, source_id, note_id);
"""

LEGACY_SCHEMA_SQL = """
CREATE TABLE notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    note_type TEXT NOT NULL DEFAULT 'general',
    is_pinned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(length(trim(title)) BETWEEN 1 AND 120),
    CHECK(length(content) <= 100000),
    CHECK(note_type IN ('general', 'meeting_minutes', 'research_note')),
    CHECK(is_pinned IN (0, 1))
);
CREATE TABLE note_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL,
    source_app TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_title_snapshot TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE,
    UNIQUE(note_id, source_app, source_type, source_id)
);
CREATE INDEX idx_notes_listing ON notes(is_pinned DESC, updated_at DESC, id DESC);
CREATE INDEX idx_note_links_note ON note_links(note_id, id);
CREATE INDEX idx_note_links_source ON note_links(source_app, source_type, source_id, note_id);
PRAGMA user_version = 1;
"""


def resolve_database_path(database_path: str | Path | None = None) -> Path:
    if database_path is not None:
        return Path(database_path).expanduser().resolve()
    configured = os.getenv("NOTES_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_DB_PATH


def _configure_connection(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")


def initialize_database(database_path: str | Path) -> Path:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        _configure_connection(connection)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, NOTES_SCHEMA_VERSION}:
            raise RuntimeError(
                f"unsupported Notes database version: {version}; "
                f"expected {NOTES_SCHEMA_VERSION}"
            )
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if version == 0 and existing_tables:
            raise RuntimeError(
                "Notes database has an unversioned schema; "
                "an explicit migration is required"
            )
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{SCHEMA_SQL}\n"
                f"PRAGMA user_version = {NOTES_SCHEMA_VERSION};\n"
                "COMMIT;"
            )
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
    verify_database(path)
    return path


def initialize_legacy_database(database_path: str | Path) -> Path:
    """Create a v1 source fixture for offline migration tests/tools only."""
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        _configure_connection(connection)
        if connection.execute("PRAGMA user_version").fetchone()[0] != 0:
            raise RuntimeError("legacy Notes database target must be empty")
        connection.executescript(LEGACY_SCHEMA_SQL)
    verify_database(path)
    return path


def verify_database(
    database_path: str | Path,
    *,
    require_current: bool = False,
    require_idempotency: bool = False,
) -> None:
    path = Path(database_path)
    if not path.is_file():
        raise RuntimeError(f"Notes database does not exist: {path}")
    with sqlite3.connect(
        f"file:{path.resolve()}?mode=ro",
        uri=True,
    ) as connection:
        _configure_connection(connection)
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(f"Notes database quick_check failed: {quick_check}")
        foreign_key_errors = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        if foreign_key_errors:
            raise RuntimeError("Notes database foreign_key_check failed")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {1, 2, NOTES_SCHEMA_VERSION}:
            raise RuntimeError(
                f"unsupported Notes database version: {version}; "
                f"expected 1, 2, or {NOTES_SCHEMA_VERSION}"
            )
        if version == 1:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            legacy_columns = {
                "notes": {
                    "id", "title", "content", "note_type", "is_pinned",
                    "created_at", "updated_at",
                },
                "note_links": {
                    "id", "note_id", "source_app", "source_type", "source_id",
                    "source_title_snapshot", "created_at",
                },
            }
            for table, columns in legacy_columns.items():
                if table not in tables:
                    raise RuntimeError(f"Notes database is missing table: {table}")
                actual = {
                    row[1]
                    for row in connection.execute(f"PRAGMA table_info({table})")
                }
                if missing := columns - actual:
                    raise RuntimeError(
                        f"Notes database table {table} is missing columns: {sorted(missing)}"
                    )
            legacy_indexes = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'index'"
                )
            }
            required_legacy_indexes = {
                "idx_notes_listing", "idx_note_links_note", "idx_note_links_source"
            }
            if missing := required_legacy_indexes - legacy_indexes:
                raise RuntimeError(
                    f"Notes database is missing indexes: {sorted(missing)}"
                )
            foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(note_links)"
            ).fetchall()
            if not any(
                row[2] == "notes" and row[3] == "note_id" and row[4] == "id"
                and row[6].upper() == "CASCADE"
                for row in foreign_keys
            ):
                raise RuntimeError("Notes database is missing the internal link foreign key")
            if require_current:
                raise RuntimeError(
                    "Notes database v1 is legacy-only; promote the verified v2 "
                    "staging database before enabling workspace routes"
                )
            # Phase 6 binaries must remain startable while Phase 5's staged v2
            # database awaits coordinated promotion. Legacy schema validation
            # remains the v1 binary's responsibility; workspace routes call
            # this function with require_current=True before being installed.
            return
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        for table, required_columns in REQUIRED_COLUMNS.items():
            if table not in tables:
                raise RuntimeError(f"Notes database is missing table: {table}")
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            missing = required_columns - columns
            if missing:
                raise RuntimeError(
                    f"Notes database table {table} is missing columns: "
                    f"{sorted(missing)}"
                )
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        missing_indexes = REQUIRED_INDEXES - indexes
        if missing_indexes:
            raise RuntimeError(
                f"Notes database is missing indexes: {sorted(missing_indexes)}"
            )
        if version == 2 and require_idempotency:
            raise RuntimeError(
                "Notes database v2 requires the explicit stopped-service v3 upgrade"
            )
        if version == NOTES_SCHEMA_VERSION:
            if "note_idempotency_keys" not in tables:
                raise RuntimeError(
                    "Notes database is missing table: note_idempotency_keys"
                )
            idempotency_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(note_idempotency_keys)"
                )
            }
            if missing := IDEMPOTENCY_REQUIRED_COLUMNS - idempotency_columns:
                raise RuntimeError(
                    "Notes database table note_idempotency_keys is missing columns: "
                    f"{sorted(missing)}"
                )
            if missing := IDEMPOTENCY_REQUIRED_INDEXES - indexes:
                raise RuntimeError(
                    f"Notes database is missing indexes: {sorted(missing)}"
                )
            idempotency_foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(note_idempotency_keys)"
            ).fetchall()
            referenced_columns = {
                (row[3], row[4], row[6].upper())
                for row in idempotency_foreign_keys
                if row[2] == "notes"
            }
            if referenced_columns != {
                ("workspace_id", "workspace_id", "CASCADE"),
                ("note_id", "id", "CASCADE"),
            }:
                raise RuntimeError(
                    "Notes database has incompatible idempotency foreign key"
                )
        note_columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(notes)")
        }
        if (
            note_columns["id"][5] != 1
            or note_columns["title"][3] != 1
            or note_columns["content"][3] != 1
            or note_columns["note_type"][3] != 1
            or note_columns["is_pinned"][3] != 1
            or note_columns["workspace_id"][3] != 1
            or note_columns["created_by"][3] != 1
            or note_columns["updated_by"][3] != 1
        ):
            raise RuntimeError("Notes database has incompatible column constraints")
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(note_links)"
        ).fetchall()
        if not any(
            row[2] == "notes"
            and row[3] == "note_id"
            and row[4] == "id"
            and row[6].upper() == "CASCADE"
            for row in foreign_keys
        ):
            raise RuntimeError("Notes database is missing the internal link foreign key")
        unique_links = any(
            row[2]
            and [
                item[2]
                for item in connection.execute(
                    f"PRAGMA index_info({row[1]})"
                )
            ] == ["workspace_id", "note_id", "source_app", "source_type", "source_id"]
            for row in connection.execute("PRAGMA index_list(note_links)")
        )
        if not unique_links:
            raise RuntimeError("Notes database is missing link uniqueness")
        schema_sql = " ".join(
            row[0].lower()
            for row in connection.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name IN ('notes', 'note_links')"
            )
        )
        for required_check in (
            "check(length(trim(title)) between 1 and 120)",
            "check(length(content) <= 100000)",
            "check(note_type in ('general', 'meeting_minutes', 'research_note'))",
            "check(is_pinned in (0, 1))",
        ):
            if required_check not in schema_sql:
                raise RuntimeError(
                    f"Notes database is missing constraint: {required_check}"
                )


@contextmanager
def database_connection(
    database_path: str | Path,
) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path)
    _configure_connection(connection)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
