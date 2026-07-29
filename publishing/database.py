from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = WORKSPACE_ROOT / "instance" / "publishing" / "publishing.db"
PUBLISHING_SCHEMA_VERSION = 1

REQUIRED_COLUMNS = {
    "publications": {
        "id",
        "note_id",
        "type_id",
        "slug",
        "title",
        "summary",
        "status",
        "latest_version",
        "published_version",
        "draft_version",
        "published_at",
        "archived_at",
        "created_at",
        "updated_at",
    },
    "publication_versions": {
        "id",
        "publication_id",
        "version",
        "title",
        "summary",
        "markdown",
        "source_note_updated_at",
        "created_at",
    },
}
REQUIRED_INDEXES = {
    "idx_publications_note",
    "idx_publications_status_updated",
    "idx_publication_versions_publication",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL,
    type_id TEXT NOT NULL,
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    latest_version INTEGER NOT NULL,
    published_version INTEGER,
    draft_version INTEGER,
    published_at TEXT,
    archived_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(status IN ('draft', 'published', 'archived')),
    CHECK(latest_version >= 1),
    CHECK(published_version IS NULL OR published_version BETWEEN 1 AND latest_version),
    CHECK(draft_version IS NULL OR draft_version BETWEEN 1 AND latest_version),
    CHECK(published_version IS NULL OR draft_version IS NULL OR draft_version > published_version),
    CHECK(status != 'draft' OR (published_version IS NULL AND draft_version IS NOT NULL)),
    CHECK(status != 'published' OR published_version IS NOT NULL),
    UNIQUE(type_id, slug)
);

CREATE TABLE IF NOT EXISTS publication_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    publication_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    markdown TEXT NOT NULL,
    source_note_updated_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(publication_id) REFERENCES publications(id) ON DELETE CASCADE,
    CHECK(version >= 1),
    UNIQUE(publication_id, version)
);

CREATE INDEX IF NOT EXISTS idx_publications_note
ON publications(note_id, type_id, updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_publications_status_updated
ON publications(status, updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_publication_versions_publication
ON publication_versions(publication_id, version DESC);
"""


def resolve_database_path(database_path: str | Path | None = None) -> Path:
    if database_path is not None:
        return Path(database_path).expanduser().resolve()
    configured = os.getenv("PUBLISHING_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_DB_PATH


def _configure_connection(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")


def initialize_database(database_path: str | Path | None = None) -> Path:
    path = resolve_database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        _configure_connection(connection)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, PUBLISHING_SCHEMA_VERSION}:
            raise RuntimeError(
                f"unsupported Publishing database version: {version}; "
                f"expected {PUBLISHING_SCHEMA_VERSION}"
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
                "Publishing database has an unversioned schema; "
                "an explicit migration is required"
            )
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{SCHEMA_SQL}\n"
                f"PRAGMA user_version = {PUBLISHING_SCHEMA_VERSION};\n"
                "COMMIT;"
            )
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
    verify_database(path)
    return path


def _schema_objects(connection: sqlite3.Connection, object_type: str) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ?", (object_type,)
        )
    }


def verify_pointer_invariants(connection: sqlite3.Connection) -> None:
    failures: list[str] = []
    publications = connection.execute(
        "SELECT id, status, latest_version, published_version, draft_version "
        "FROM publications ORDER BY id"
    ).fetchall()
    for publication in publications:
        publication_id = publication["id"]
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM publication_versions "
                "WHERE publication_id = ? ORDER BY version",
                (publication_id,),
            )
        ]
        if not versions:
            failures.append(f"publication {publication_id} has no versions")
            continue
        latest = publication["latest_version"]
        if latest != max(versions):
            failures.append(
                f"publication {publication_id} latest_version={latest} "
                f"does not equal max version={max(versions)}"
            )
        for pointer_name in ("published_version", "draft_version"):
            pointer = publication[pointer_name]
            if pointer is not None and pointer not in versions:
                failures.append(
                    f"publication {publication_id} {pointer_name}={pointer} "
                    "does not reference an existing version"
                )
        published = publication["published_version"]
        draft = publication["draft_version"]
        if published is not None and draft is not None and draft <= published:
            failures.append(
                f"publication {publication_id} draft_version must exceed "
                "published_version"
            )
        if publication["status"] == "draft" and (
            published is not None or draft is None
        ):
            failures.append(f"publication {publication_id} has invalid draft pointers")
        if publication["status"] == "published" and published is None:
            failures.append(
                f"publication {publication_id} has no published_version"
            )
    if failures:
        raise RuntimeError("Publishing pointer invariant failed: " + "; ".join(failures))


def verify_projection_source_of_truth(connection: sqlite3.Connection) -> None:
    failures = connection.execute(
        """
        SELECT p.id, p.title, p.summary, v.title, v.summary
        FROM publications AS p
        LEFT JOIN publication_versions AS v
          ON v.publication_id = p.id
         AND v.version = COALESCE(
             p.draft_version, p.published_version, p.latest_version
         )
        WHERE v.id IS NULL OR p.title != v.title OR p.summary != v.summary
        ORDER BY p.id
        """
    ).fetchall()
    if failures:
        ids = ", ".join(str(row[0]) for row in failures)
        raise RuntimeError(
            "Publishing projection source-of-truth mismatch for publications: " + ids
        )


def verify_database(database_path: str | Path | None = None) -> None:
    path = resolve_database_path(database_path)
    if not path.is_file():
        raise RuntimeError(f"Publishing database does not exist: {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        _configure_connection(connection)
        quick_check = connection.execute("PRAGMA quick_check").fetchone()[0]
        if quick_check != "ok":
            raise RuntimeError(
                f"Publishing database quick_check failed: {quick_check}"
            )
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Publishing database foreign_key_check failed")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != PUBLISHING_SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported Publishing database version: {version}; "
                f"expected {PUBLISHING_SCHEMA_VERSION}"
            )
        tables = _schema_objects(connection, "table")
        for table, required_columns in REQUIRED_COLUMNS.items():
            if table not in tables:
                raise RuntimeError(f"Publishing database is missing table: {table}")
            columns = {
                row[1] for row in connection.execute(f"PRAGMA table_info({table})")
            }
            missing = required_columns - columns
            if missing:
                raise RuntimeError(
                    f"Publishing database table {table} is missing columns: "
                    f"{sorted(missing)}"
                )
        indexes = _schema_objects(connection, "index")
        missing_indexes = REQUIRED_INDEXES - indexes
        if missing_indexes:
            raise RuntimeError(
                f"Publishing database is missing indexes: {sorted(missing_indexes)}"
            )
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(publication_versions)"
        ).fetchall()
        if len(foreign_keys) != 1 or not (
            foreign_keys[0][2] == "publications"
            and foreign_keys[0][3] == "publication_id"
            and foreign_keys[0][4] == "id"
            and foreign_keys[0][6].upper() == "CASCADE"
        ):
            raise RuntimeError(
                "Publishing database has an incompatible internal foreign key"
            )
        if connection.execute("PRAGMA foreign_key_list(publications)").fetchall():
            raise RuntimeError("Publishing publications must not have foreign keys")
        schema_sql = " ".join(
            row[0].lower()
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name IN ('publications', 'publication_versions')"
            )
        )
        for required_check in (
            "check(status in ('draft', 'published', 'archived'))",
            "check(latest_version >= 1)",
            "check(version >= 1)",
        ):
            if required_check not in schema_sql:
                raise RuntimeError(
                    f"Publishing database is missing constraint: {required_check}"
                )
        verify_pointer_invariants(connection)
        verify_projection_source_of_truth(connection)


@contextmanager
def database_connection(
    database_path: str | Path | None = None,
) -> Iterator[sqlite3.Connection]:
    path = resolve_database_path(database_path)
    connection = sqlite3.connect(path)
    _configure_connection(connection)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
