from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from assets.database import (
    ASSET_SCHEMA_VERSION,
    DEFAULT_DB_PATH,
    database_connection,
    initialize_database,
    resolve_database_path,
    verify_database,
)


def test_initializes_independent_verified_asset_schema(tmp_path: Path) -> None:
    path = initialize_database(tmp_path / "assets.db")

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == ASSET_SCHEMA_VERSION
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        } >= {"assets", "asset_references"}
        assert connection.execute("PRAGMA foreign_key_list(assets)").fetchall() == []
        foreign_keys = connection.execute("PRAGMA foreign_key_list(asset_references)").fetchall()
        assert len(foreign_keys) == 1
        assert foreign_keys[0][2] == "assets"
        schema = " ".join(
            row[0].lower()
            for row in connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name IN ('assets','asset_references')"
            )
        )
        assert "notes" not in schema
        assert "publications" not in schema
    verify_database(path)


def test_path_injection_does_not_create_default_or_environment_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured" / "assets.db"
    explicit = tmp_path / "tests" / "assets.db"
    monkeypatch.setenv("ASSET_DB_PATH", str(configured))

    assert resolve_database_path() == configured.resolve()
    assert resolve_database_path(explicit) == explicit.resolve()
    initialize_database(explicit)

    assert explicit.is_file()
    assert not configured.exists()
    monkeypatch.delenv("ASSET_DB_PATH")
    assert resolve_database_path() == DEFAULT_DB_PATH


def test_rejects_unknown_and_unversioned_schemas(tmp_path: Path) -> None:
    future = tmp_path / "future.db"
    with sqlite3.connect(future) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="unsupported Asset"):
        initialize_database(future)

    malformed = tmp_path / "malformed.db"
    with sqlite3.connect(malformed) as connection:
        connection.execute("CREATE TABLE assets (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="explicit migration"):
        initialize_database(malformed)


def test_internal_foreign_key_cascades_references_only(tmp_path: Path) -> None:
    path = initialize_database(tmp_path / "assets.db")
    with database_connection(path) as connection:
        cursor = connection.execute(
            "INSERT INTO assets (sha256,media_type,byte_size,storage_key,original_name,created_at) "
            "VALUES (?, 'image/png', 1, ?, 'x.png', '2026-07-30T00:00:00Z')",
            ("a" * 64, f"aa/aa/{'a' * 64}.png"),
        )
        connection.execute(
            "INSERT INTO asset_references (asset_id,owner_type,owner_id,field,created_at) "
            "VALUES (?, 'note', '1', 'content', '2026-07-30T00:00:00Z')",
            (cursor.lastrowid,),
        )
        connection.execute("DELETE FROM assets WHERE id = ?", (cursor.lastrowid,))
        assert connection.execute("SELECT COUNT(*) FROM asset_references").fetchone()[0] == 0
