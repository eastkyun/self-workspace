from pathlib import Path

from apps.calendar.app.main import DEFAULT_DB_PATH, WORKSPACE_ROOT, resolve_database_path


def test_default_database_path_is_workspace_instance(monkeypatch) -> None:
    monkeypatch.delenv("CALENDAR_DB_PATH", raising=False)
    monkeypatch.delenv("TODO_DB_PATH", raising=False)

    assert resolve_database_path(None) == DEFAULT_DB_PATH
    assert DEFAULT_DB_PATH == WORKSPACE_ROOT / "instance" / "calendar" / "tasks.db"


def test_database_path_precedence(monkeypatch, tmp_path: Path) -> None:
    legacy_path = tmp_path / "legacy.db"
    calendar_path = tmp_path / "calendar.db"
    explicit_path = tmp_path / "explicit.db"
    monkeypatch.setenv("TODO_DB_PATH", str(legacy_path))
    monkeypatch.setenv("CALENDAR_DB_PATH", str(calendar_path))

    assert resolve_database_path(None) == calendar_path
    assert resolve_database_path(explicit_path) == explicit_path


def test_relative_database_path_is_workspace_relative(monkeypatch) -> None:
    monkeypatch.setenv("CALENDAR_DB_PATH", "instance/calendar/custom.db")

    assert resolve_database_path(None) == (
        WORKSPACE_ROOT / "instance" / "calendar" / "custom.db"
    )
