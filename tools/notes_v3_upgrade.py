"""Atomic, stopped-service promotion of a Notes v2 database to v3."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from apps.notes.app.database import verify_database
from apps.notes.idempotency_migration import MIGRATIONS, TARGET_VERSION
from shared.migrations import migrate


@dataclass(frozen=True, slots=True)
class NotesUpgradeResult:
    database: Path
    backup: Path | None
    changed: bool


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def upgrade_notes_v3(database_path: str | Path) -> NotesUpgradeResult:
    path = Path(database_path).expanduser().resolve()
    if os.environ.get("WORKSPACE_SINGLE_WORKER_ASSERTED", "").lower() != "true":
        raise RuntimeError("WORKSPACE_SINGLE_WORKER_ASSERTED=true is required")
    if not path.is_file():
        raise RuntimeError(f"Notes database does not exist: {path}")
    for suffix in ("-wal", "-shm"):
        if Path(f"{path}{suffix}").exists():
            raise RuntimeError("Notes database has live WAL state; stop all services first")

    lock_path = Path(f"{path}.v3-upgrade.lock")
    try:
        lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError("another Notes v3 upgrade is already in progress") from error

    staging = path.with_name(f".{path.name}.{uuid4().hex}.v3-staging")
    backup: Path | None = None
    try:
        verify_database(path, require_current=True)
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as source:
            version = source.execute("PRAGMA user_version").fetchone()[0]
        if version == TARGET_VERSION:
            verify_database(path, require_idempotency=True)
            return NotesUpgradeResult(path, None, False)
        if version != 2:
            raise RuntimeError(f"Notes v3 upgrade requires schema v2; found v{version}")

        source_digest = _digest(path)
        shutil.copy2(path, staging)
        with sqlite3.connect(staging) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA busy_timeout = 5000")
            migrate(
                connection,
                target_version=TARGET_VERSION,
                migrations=MIGRATIONS,
                context={},
            )
        verify_database(staging, require_idempotency=True)
        if _digest(path) != source_digest:
            raise RuntimeError("Notes database changed while the v3 staging copy was prepared")

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = path.with_name(f"{path.name}.v2-backup-{timestamp}")
        if backup.exists():
            raise RuntimeError(f"Notes backup already exists: {backup.name}")
        os.replace(path, backup)
        try:
            os.replace(staging, path)
            _fsync_directory(path.parent)
            verify_database(path, require_idempotency=True)
        except BaseException:
            os.replace(backup, path)
            _fsync_directory(path.parent)
            backup = None
            raise
        return NotesUpgradeResult(path, backup, True)
    finally:
        os.close(lock_descriptor)
        lock_path.unlink(missing_ok=True)
        staging.unlink(missing_ok=True)
