from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = WORKSPACE_ROOT / "instance" / "assets" / "assets.db"
ASSET_SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sha256 TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    storage_key TEXT NOT NULL UNIQUE,
    original_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK(length(sha256) = 64),
    CHECK(byte_size > 0),
    CHECK(media_type IN ('image/png', 'image/jpeg', 'image/webp'))
);

CREATE TABLE IF NOT EXISTS asset_references (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    field TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE,
    UNIQUE(asset_id, owner_type, owner_id, field)
);

CREATE INDEX IF NOT EXISTS idx_asset_references_owner
ON asset_references(owner_type, owner_id, field, asset_id);
"""

REQUIRED_COLUMNS = {
    "assets": {"id", "sha256", "media_type", "byte_size", "storage_key", "original_name", "created_at"},
    "asset_references": {"id", "asset_id", "owner_type", "owner_id", "field", "created_at"},
}


def resolve_database_path(database_path: str | Path | None = None) -> Path:
    if database_path is not None:
        return Path(database_path).expanduser().resolve()
    configured = os.getenv("ASSET_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return DEFAULT_DB_PATH


def _configure(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")


def initialize_database(database_path: str | Path | None = None) -> Path:
    path = resolve_database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        _configure(connection)
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in {0, ASSET_SCHEMA_VERSION}:
            raise RuntimeError(
                f"unsupported Asset database version: {version}; expected {ASSET_SCHEMA_VERSION}"
            )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if version == 0 and tables:
            raise RuntimeError("Asset database has an unversioned schema; an explicit migration is required")
        try:
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                f"{SCHEMA_SQL}\n"
                f"PRAGMA user_version = {ASSET_SCHEMA_VERSION};\n"
                "COMMIT;"
            )
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
    verify_database(path)
    return path


def verify_database(database_path: str | Path | None = None) -> None:
    path = resolve_database_path(database_path)
    if not path.is_file():
        raise RuntimeError(f"Asset database does not exist: {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        _configure(connection)
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Asset database quick_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Asset database foreign_key_check failed")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != ASSET_SCHEMA_VERSION:
            raise RuntimeError(
                f"unsupported Asset database version: {version}; expected {ASSET_SCHEMA_VERSION}"
            )
        for table, expected in REQUIRED_COLUMNS.items():
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            missing = expected - columns
            if missing:
                raise RuntimeError(f"Asset database table {table} is missing columns: {sorted(missing)}")
        foreign_keys = connection.execute("PRAGMA foreign_key_list(asset_references)").fetchall()
        if len(foreign_keys) != 1 or not (
            foreign_keys[0][2] == "assets"
            and foreign_keys[0][3] == "asset_id"
            and foreign_keys[0][4] == "id"
            and foreign_keys[0][6].upper() == "CASCADE"
        ):
            raise RuntimeError("Asset database has an incompatible internal foreign key")
        if connection.execute("PRAGMA foreign_key_list(assets)").fetchall():
            raise RuntimeError("Asset records must not have foreign keys")
        indexes = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        if "idx_asset_references_owner" not in indexes:
            raise RuntimeError("Asset database is missing owner reference index")


def initialize_workspace_database(database_path: str | Path | None = None) -> Path:
    """Create an empty Asset database directly at tenant-aware schema v2."""
    path = resolve_database_path(database_path)
    if path.exists():
        verify_workspace_database(path)
        return path
    initialize_database(path)
    from assets.migrations import MIGRATIONS, TARGET_VERSION
    from shared.migrations import migrate

    bootstrap_id = "00000000-0000-4000-8000-000000000001"
    with sqlite3.connect(path) as connection:
        _configure(connection)
        migrate(
            connection,
            target_version=TARGET_VERSION,
            migrations=MIGRATIONS,
            context={
                "workspace_id": bootstrap_id,
                "actor_id": bootstrap_id,
                "release_id": bootstrap_id,
                "asset_public_ids": {},
            },
        )
    verify_workspace_database(path)
    return path


def verify_workspace_database(database_path: str | Path | None = None) -> None:
    """Fail closed unless Assets is at tenant-aware schema v2."""
    path = resolve_database_path(database_path)
    if not path.is_file():
        raise RuntimeError(f"Asset database does not exist: {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        _configure(connection)
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Asset database quick_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Asset database foreign_key_check failed")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != 2:
            raise RuntimeError(
                "asset database requires an offline migration: "
                f"expected schema v2, found v{version}"
            )
        required = {
            "assets": {"workspace_id", "uploaded_by", "public_id"},
            "asset_references": {"workspace_id"},
        }
        for table, expected in required.items():
            columns = {
                row[1]: row for row in connection.execute(f"PRAGMA table_info({table})")
            }
            if not expected.issubset(columns) or any(
                columns[name][3] != 1 for name in expected
            ):
                raise RuntimeError(f"Asset workspace schema is invalid: {table}")
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        if not {
            "idx_assets_workspace_created",
            "idx_asset_references_workspace_owner",
        }.issubset(indexes):
            raise RuntimeError("Asset workspace indexes are missing")


@contextmanager
def database_connection(database_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(resolve_database_path(database_path))
    _configure(connection)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
