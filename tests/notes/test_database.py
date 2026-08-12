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
                title, content, note_type, is_pinned, created_at, updated_at,
                workspace_id, created_by, updated_by
            ) VALUES ('Note', '', 'general', 0, '2026-07-29T00:00:00+00:00',
                      '2026-07-29T00:00:00+00:00', 'w', 'u', 'u')
            """
        )
        connection.execute(
            """
            INSERT INTO note_links (
                note_id, source_app, source_type, source_id,
                source_title_snapshot, created_at, workspace_id
            ) VALUES (?, 'calendar', 'event', '1', 'Event',
                      '2026-07-29T00:00:00+00:00', 'w')
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


def test_v1_staged_cutover_compatibility_and_workspace_route_guard(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE notes (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                note_type TEXT NOT NULL, is_pinned INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE note_links (
                id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL,
                source_app TEXT NOT NULL, source_type TEXT NOT NULL,
                source_id TEXT NOT NULL, source_title_snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(note_id) REFERENCES notes(id) ON DELETE CASCADE
            );
            CREATE INDEX idx_notes_listing ON notes(is_pinned, updated_at, id);
            CREATE INDEX idx_note_links_note ON note_links(note_id, id);
            CREATE INDEX idx_note_links_source
                ON note_links(source_app, source_type, source_id, note_id);
            PRAGMA user_version=1;
            """
        )

    # Pre-cutover processes remain startable while the v2 staging copy awaits
    # coordinated promotion, but canonical workspace routes cannot activate.
    verify_database(path)
    with pytest.raises(RuntimeError, match="legacy-only"):
        verify_database(path, require_current=True)
