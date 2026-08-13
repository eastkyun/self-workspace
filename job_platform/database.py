from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

JOBS_SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE jobs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 120),
    job_type TEXT NOT NULL CHECK(length(job_type) BETWEEN 1 AND 64),
    status TEXT NOT NULL CHECK(status IN ('DRAFT','ACTIVE','PAUSED','ARCHIVED')),
    schedule_kind TEXT NOT NULL CHECK(schedule_kind IN ('MANUAL','INTERVAL','CRON')),
    schedule_expression TEXT,
    timezone TEXT CHECK(timezone IS NULL OR length(timezone) BETWEEN 1 AND 64),
    next_run_at TEXT,
    last_run_at TEXT,
    last_success_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
    max_consecutive_failures INTEGER NOT NULL DEFAULT 5 CHECK(max_consecutive_failures BETWEEN 1 AND 20),
    definition_revision INTEGER NOT NULL DEFAULT 1 CHECK(definition_revision >= 1),
    last_tested_revision INTEGER CHECK(last_tested_revision IS NULL OR last_tested_revision >= 1),
    last_tested_at TEXT,
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, id),
    CHECK((last_tested_revision IS NULL) = (last_tested_at IS NULL)),
    CHECK(status != 'DRAFT' OR next_run_at IS NULL),
    CHECK(
      (schedule_kind = 'MANUAL' AND schedule_expression IS NULL AND timezone IS NULL AND next_run_at IS NULL)
      OR (schedule_kind = 'INTERVAL' AND CAST(schedule_expression AS INTEGER) >= 300 AND timezone IS NULL)
      OR (schedule_kind = 'CRON' AND schedule_expression IS NOT NULL AND timezone IS NOT NULL)
    ),
    CHECK(status != 'ACTIVE' OR schedule_kind = 'MANUAL' OR next_run_at IS NOT NULL)
);

CREATE TABLE job_runs (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    trigger_kind TEXT NOT NULL CHECK(trigger_kind IN ('MANUAL','SCHEDULED','RETRY')),
    dedupe_key TEXT NOT NULL CHECK(length(dedupe_key) BETWEEN 1 AND 160),
    scheduled_for TEXT NOT NULL,
    source_run_id TEXT,
    status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','UNCHANGED','PARTIAL','FAILED','CANCELLED')),
    attempt INTEGER NOT NULL CHECK(attempt >= 1),
    definition_revision INTEGER NOT NULL CHECK(definition_revision >= 1),
    worker_id TEXT,
    lease_token TEXT,
    lease_until TEXT,
    started_at TEXT,
    heartbeat_at TEXT,
    finished_at TEXT,
    outcome_code TEXT CHECK(outcome_code IS NULL OR length(outcome_code) <= 64),
    error_code TEXT CHECK(error_code IS NULL OR length(error_code) <= 64),
    error_message TEXT CHECK(error_message IS NULL OR length(error_message) <= 500),
    created_at TEXT NOT NULL,
    UNIQUE(job_id, dedupe_key),
    UNIQUE(workspace_id, id),
    FOREIGN KEY(workspace_id, job_id) REFERENCES jobs(workspace_id, id) ON DELETE CASCADE,
    FOREIGN KEY(source_run_id) REFERENCES job_runs(id),
    CHECK((trigger_kind = 'RETRY') = (source_run_id IS NOT NULL)),
    CHECK(
      (status = 'RUNNING' AND worker_id IS NOT NULL AND lease_token IS NOT NULL AND lease_until IS NOT NULL AND started_at IS NOT NULL AND heartbeat_at IS NOT NULL AND finished_at IS NULL)
      OR (status != 'RUNNING' AND lease_token IS NULL AND lease_until IS NULL)
    ),
    CHECK((status IN ('SUCCEEDED','UNCHANGED','PARTIAL','FAILED','CANCELLED')) = (finished_at IS NOT NULL)),
    CHECK(status NOT IN ('QUEUED','RUNNING') OR (outcome_code IS NULL AND error_code IS NULL AND error_message IS NULL))
);

CREATE TABLE job_worker_invocations (
    id TEXT PRIMARY KEY,
    worker_id TEXT NOT NULL,
    version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('RUNNING','SUCCEEDED','FAILED','STOPPED')),
    claimed_count INTEGER NOT NULL DEFAULT 0 CHECK(claimed_count >= 0),
    completed_count INTEGER NOT NULL DEFAULT 0 CHECK(completed_count >= 0),
    failed_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_count >= 0),
    error_code TEXT CHECK(error_code IS NULL OR length(error_code) <= 64)
);

CREATE INDEX idx_jobs_due ON jobs(status, next_run_at, id);
CREATE INDEX idx_jobs_workspace_status ON jobs(workspace_id, status, updated_at DESC, id);
CREATE INDEX idx_jobs_workspace_creator ON jobs(workspace_id, created_by, id);
CREATE INDEX idx_job_runs_queue ON job_runs(status, scheduled_for, id);
CREATE INDEX idx_job_runs_history ON job_runs(workspace_id, job_id, created_at DESC, id);
CREATE INDEX idx_job_runs_stale ON job_runs(lease_until, id) WHERE status = 'RUNNING';
CREATE UNIQUE INDEX idx_job_runs_one_active ON job_runs(job_id) WHERE status IN ('QUEUED','RUNNING');
CREATE INDEX idx_worker_invocations_freshness ON job_worker_invocations(status, heartbeat_at DESC);
"""

REQUIRED_TABLES = frozenset({"jobs", "job_runs", "job_worker_invocations"})
REQUIRED_INDEXES = frozenset(
    {
        "idx_jobs_due",
        "idx_jobs_workspace_status",
        "idx_jobs_workspace_creator",
        "idx_job_runs_queue",
        "idx_job_runs_history",
        "idx_job_runs_stale",
        "idx_job_runs_one_active",
        "idx_worker_invocations_freshness",
    }
)


def connect(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(path), isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    return connection


@contextmanager
def transaction(path: str | Path, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database(path: str | Path) -> Path:
    database = Path(path).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    with connect(database) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        if version == 0 and tables:
            raise RuntimeError("Jobs database has an unversioned schema; explicit migration required")
        if version not in {0, JOBS_SCHEMA_VERSION}:
            raise RuntimeError(
                f"unsupported Jobs database version: {version}; expected {JOBS_SCHEMA_VERSION}"
            )
        if version == 0:
            connection.executescript(
                "BEGIN IMMEDIATE;\n" + SCHEMA_SQL + f"\nPRAGMA user_version={JOBS_SCHEMA_VERSION};\nCOMMIT;"
            )
    verify_database(database)
    return database


def verify_database(path: str | Path) -> None:
    with connect(path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != JOBS_SCHEMA_VERSION:
            raise RuntimeError(f"Jobs database requires schema v{JOBS_SCHEMA_VERSION}")
        tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        indexes = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")
        }
        if missing := REQUIRED_TABLES - tables:
            raise RuntimeError(f"Jobs database missing tables: {', '.join(sorted(missing))}")
        if missing := REQUIRED_INDEXES - indexes:
            raise RuntimeError(f"Jobs database missing indexes: {', '.join(sorted(missing))}")
        failures = connection.execute("PRAGMA foreign_key_check").fetchall()
        if failures:
            raise RuntimeError("Jobs database foreign key verification failed")
