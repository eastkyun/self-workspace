from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from publishing.database import (
    DEFAULT_DB_PATH,
    PUBLISHING_SCHEMA_VERSION,
    database_connection,
    initialize_database,
    resolve_database_path,
    verify_database,
    verify_pointer_invariants,
    verify_projection_source_of_truth,
)
from publishing.repository import create_publication


def test_initialize_database_creates_verified_schema(tmp_path: Path) -> None:
    path = initialize_database(tmp_path / "publishing.db")

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == PUBLISHING_SCHEMA_VERSION
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {"publications", "publication_versions"} <= tables
        assert connection.execute("PRAGMA foreign_key_list(publications)").fetchall() == []
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(publication_versions)"
        ).fetchall()
        assert len(foreign_keys) == 1
        assert foreign_keys[0][2:7] == (
            "publications",
            "publication_id",
            "id",
            "NO ACTION",
            "CASCADE",
        )

    verify_database(path)


def test_path_resolution_uses_explicit_then_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured.db"
    explicit = tmp_path / "explicit.db"
    monkeypatch.setenv("PUBLISHING_DB_PATH", str(configured))

    assert resolve_database_path() == configured.resolve()
    assert resolve_database_path(explicit) == explicit.resolve()
    monkeypatch.delenv("PUBLISHING_DB_PATH")
    assert resolve_database_path() == DEFAULT_DB_PATH


def test_tmp_database_does_not_create_production_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    production = tmp_path / "production" / "publishing.db"
    test_path = tmp_path / "tests" / "publishing.db"
    monkeypatch.setenv("PUBLISHING_DB_PATH", str(production))

    initialize_database(test_path)

    assert test_path.is_file()
    assert not production.exists()


def test_database_connection_rolls_back_on_error(tmp_path: Path) -> None:
    path = initialize_database(tmp_path / "publishing.db")

    with pytest.raises(RuntimeError):
        with database_connection(path) as connection:
            create_publication(
                connection,
                note_id=1,
                type_id="manual",
                slug="install",
                title="Install",
                summary="Summary",
                markdown="# Install",
                source_note_updated_at="2026-07-30T00:00:00Z",
                created_at="2026-07-30T00:00:00Z",
            )
            raise RuntimeError("rollback")

    with database_connection(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 0


def _created_database(tmp_path: Path) -> Path:
    path = initialize_database(tmp_path / "publishing.db")
    with database_connection(path) as connection:
        create_publication(
            connection,
            note_id=1,
            type_id="manual",
            slug="install",
            title="Install",
            summary="Summary",
            markdown="# Install",
            source_note_updated_at="2026-07-30T00:00:00Z",
            created_at="2026-07-30T00:00:00Z",
        )
    return path


def test_pointer_verification_collects_invalid_latest_version(tmp_path: Path) -> None:
    path = _created_database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute("UPDATE publications SET latest_version = 2")
        with pytest.raises(RuntimeError, match="latest_version=2"):
            verify_pointer_invariants(connection)


def test_projection_verification_rejects_cache_mismatch(tmp_path: Path) -> None:
    path = _created_database(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("UPDATE publications SET title = 'Stale'")
        with pytest.raises(RuntimeError, match="source-of-truth mismatch"):
            verify_projection_source_of_truth(connection)


def test_unsupported_and_unversioned_database_are_rejected(tmp_path: Path) -> None:
    unsupported = tmp_path / "unsupported.db"
    with sqlite3.connect(unsupported) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="unsupported Publishing database version"):
        initialize_database(unsupported)

    unversioned = tmp_path / "unversioned.db"
    with sqlite3.connect(unversioned) as connection:
        connection.execute("CREATE TABLE legacy (id INTEGER)")
    with pytest.raises(RuntimeError, match="unversioned schema"):
        initialize_database(unversioned)
