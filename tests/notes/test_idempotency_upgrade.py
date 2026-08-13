from __future__ import annotations

import hashlib
import sqlite3
from uuid import uuid4

import pytest

from apps.notes.app.database import initialize_legacy_database, verify_database
from apps.notes.migrations import MIGRATIONS as V2_MIGRATIONS
from shared.migrations import migrate
from tools.notes_v3_upgrade import upgrade_notes_v3


def _v2_database(path):
    initialize_legacy_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        migrate(
            connection,
            target_version=2,
            migrations=V2_MIGRATIONS,
            context={"workspace_id": str(uuid4()), "actor_id": str(uuid4())},
        )
    return path


def test_v2_requires_explicit_idempotency_upgrade(tmp_path) -> None:
    path = _v2_database(tmp_path / "notes.db")

    verify_database(path, require_current=True)
    with pytest.raises(RuntimeError, match="stopped-service v3 upgrade"):
        verify_database(path, require_idempotency=True)


def test_stopped_service_upgrade_promotes_v3_and_keeps_backup(
    tmp_path, monkeypatch
) -> None:
    path = _v2_database(tmp_path / "notes.db")
    original_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setenv("WORKSPACE_SINGLE_WORKER_ASSERTED", "true")

    result = upgrade_notes_v3(path)

    assert result.changed is True
    assert result.backup is not None and result.backup.is_file()
    assert hashlib.sha256(result.backup.read_bytes()).hexdigest() == original_digest
    verify_database(path, require_idempotency=True)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3

    repeated = upgrade_notes_v3(path)
    assert repeated.changed is False
    assert repeated.backup is None


def test_upgrade_fails_closed_without_single_worker_assertion(
    tmp_path, monkeypatch
) -> None:
    path = _v2_database(tmp_path / "notes.db")
    monkeypatch.delenv("WORKSPACE_SINGLE_WORKER_ASSERTED", raising=False)

    with pytest.raises(RuntimeError, match="SINGLE_WORKER"):
        upgrade_notes_v3(path)

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
