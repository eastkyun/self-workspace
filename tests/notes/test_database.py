from pathlib import Path
import sqlite3

import pytest

from apps.notes.app.database import (
    NOTES_SCHEMA_VERSION,
    database_connection,
    initialize_database,
    resolve_database_path,
    verify_database,
)


def test_initializes_independent_notes_schema(tmp_path: Path) -> None:
    path = tmp_path / "notes.db"

    initialize_database(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == (
            NOTES_SCHEMA_VERSION
        )
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        } >= {"notes", "note_links"}
        foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(note_links)"
        ).fetchall()
        assert len(foreign_keys) == 1
        assert foreign_keys[0][2] == "notes"
        assert "calendar" not in path.read_bytes().decode("latin1").lower()


def test_connection_enables_foreign_keys_and_cascades_links(tmp_path: Path) -> None:
    path = tmp_path / "notes.db"
    initialize_database(path)

    with database_connection(path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        cursor = connection.execute(
            """
            INSERT INTO notes (
                title, content, note_type, is_pinned, created_at, updated_at
            ) VALUES ('Note', '', 'general', 0, '2026-07-29T00:00:00+00:00',
                      '2026-07-29T00:00:00+00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO note_links (
                note_id, source_app, source_type, source_id,
                source_title_snapshot, created_at
            ) VALUES (?, 'calendar', 'event', '1', 'Event',
                      '2026-07-29T00:00:00+00:00')
            """,
            (cursor.lastrowid,),
        )
        connection.execute("DELETE FROM notes WHERE id = ?", (cursor.lastrowid,))
        assert connection.execute("SELECT COUNT(*) FROM note_links").fetchone()[0] == 0


def test_resolves_explicit_then_environment_then_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    configured = tmp_path / "configured.db"
    explicit = tmp_path / "explicit.db"
    monkeypatch.setenv("NOTES_DB_PATH", str(configured))

    assert resolve_database_path(explicit) == explicit.resolve()
    assert resolve_database_path() == configured.resolve()


def test_rejects_unknown_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "future.db"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 99")

    with pytest.raises(RuntimeError, match="unsupported Notes"):
        initialize_database(path)
    with pytest.raises(RuntimeError, match="unsupported Notes"):
        verify_database(path)


def test_rejects_unversioned_existing_schema(tmp_path: Path) -> None:
    path = tmp_path / "malformed.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY)")

    with pytest.raises(RuntimeError, match="explicit migration"):
        initialize_database(path)
