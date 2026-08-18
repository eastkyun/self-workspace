from __future__ import annotations

from pathlib import Path

from job_platform.database import connect, verify_database

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS web_collection_formatter_profiles (
    id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    instructions TEXT NOT NULL CHECK(length(instructions) BETWEEN 1 AND 30000),
    model TEXT NOT NULL CHECK(length(model) BETWEEN 1 AND 120),
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    created_by TEXT NOT NULL,
    updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, id),
    UNIQUE(workspace_id, name COLLATE NOCASE)
);

CREATE TABLE IF NOT EXISTS web_collection_jobs (
    job_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    url TEXT NOT NULL CHECK(length(url) BETWEEN 1 AND 2048),
    selector TEXT NOT NULL CHECK(length(selector) BETWEEN 1 AND 512),
    selector_metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(length(selector_metadata_json) <= 16384),
    fallback_x REAL CHECK(fallback_x IS NULL OR fallback_x >= 0),
    fallback_y REAL CHECK(fallback_y IS NULL OR fallback_y >= 0),
    fallback_width REAL CHECK(fallback_width IS NULL OR fallback_width >= 0),
    fallback_height REAL CHECK(fallback_height IS NULL OR fallback_height >= 0),
    note_policy TEXT NOT NULL CHECK(note_policy IN ('CHANGED_ONLY','EVERY_RUN')),
    output_format TEXT NOT NULL DEFAULT 'RAW_MARKDOWN'
        CHECK(output_format IN ('RAW_MARKDOWN','FINANCIAL_MARKET_BRIEFING')),
    formatter_profile_id TEXT CHECK(
        formatter_profile_id IS NULL OR length(formatter_profile_id) BETWEEN 1 AND 64
    ),
    extract_links INTEGER NOT NULL CHECK(extract_links IN (0,1)),
    extract_images INTEGER NOT NULL DEFAULT 0 CHECK(extract_images = 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, job_id),
    FOREIGN KEY(workspace_id, job_id) REFERENCES jobs(workspace_id, id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS web_collection_results (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    final_url TEXT NOT NULL CHECK(length(final_url) BETWEEN 1 AND 2048),
    content_html TEXT NOT NULL CHECK(length(content_html) <= 1000000),
    content_text TEXT NOT NULL CHECK(length(content_text) <= 100000),
    content_markdown TEXT NOT NULL CHECK(length(content_markdown) <= 90000),
    content_hash TEXT NOT NULL CHECK(length(content_hash) = 64 AND content_hash = lower(content_hash)),
    previous_hash TEXT CHECK(previous_hash IS NULL OR (length(previous_hash)=64 AND previous_hash=lower(previous_hash))),
    changed INTEGER NOT NULL CHECK(changed IN (0,1)),
    note_status TEXT NOT NULL CHECK(note_status IN ('NOT_REQUIRED','PENDING','CREATED','FAILED')),
    note_id INTEGER,
    note_token TEXT,
    note_lease_until TEXT,
    error_code TEXT CHECK(error_code IS NULL OR length(error_code) <= 64),
    error_message TEXT CHECK(error_message IS NULL OR length(error_message) <= 500),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, run_id),
    FOREIGN KEY(workspace_id, job_id) REFERENCES web_collection_jobs(workspace_id, job_id) ON DELETE CASCADE,
    FOREIGN KEY(workspace_id, run_id) REFERENCES job_runs(workspace_id, id) ON DELETE CASCADE,
    CHECK(
      (note_status='CREATED' AND note_id IS NOT NULL AND note_token IS NULL AND note_lease_until IS NULL)
      OR (note_status='PENDING' AND note_id IS NULL)
      OR (note_status IN ('NOT_REQUIRED','FAILED') AND note_id IS NULL AND note_token IS NULL AND note_lease_until IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_web_collection_results_history
ON web_collection_results(workspace_id,job_id,collected_at DESC,id);
CREATE INDEX IF NOT EXISTS idx_web_collection_results_note_pending
ON web_collection_results(note_status,note_lease_until,id) WHERE note_status='PENDING';
CREATE INDEX IF NOT EXISTS idx_web_collection_formatter_profiles_workspace
ON web_collection_formatter_profiles(workspace_id,updated_at DESC,id);
"""

REQUIRED_TABLES = frozenset(
    {
        "web_collection_formatter_profiles",
        "web_collection_jobs",
        "web_collection_results",
    }
)
REQUIRED_INDEXES = frozenset(
    {
        "idx_web_collection_formatter_profiles_workspace",
        "idx_web_collection_jobs_formatter_profile",
        "idx_web_collection_results_history",
        "idx_web_collection_results_note_pending",
    }
)
REQUIRED_JOB_COLUMNS = frozenset({"output_format", "formatter_profile_id"})


def initialize_database(path: str | Path) -> Path:
    database = Path(path).expanduser().resolve()
    verify_database(database)
    with connect(database) as connection:
        connection.executescript("BEGIN IMMEDIATE;\n" + SCHEMA_SQL + "\nCOMMIT;")
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(web_collection_jobs)")
        }
        if "output_format" not in columns:
            connection.execute(
                """ALTER TABLE web_collection_jobs ADD COLUMN output_format TEXT
                   NOT NULL DEFAULT 'RAW_MARKDOWN'
                   CHECK(output_format IN ('RAW_MARKDOWN','FINANCIAL_MARKET_BRIEFING'))"""
            )
        if "formatter_profile_id" not in columns:
            connection.execute(
                """ALTER TABLE web_collection_jobs ADD COLUMN formatter_profile_id TEXT
                   CHECK(formatter_profile_id IS NULL OR length(formatter_profile_id) BETWEEN 1 AND 64)"""
            )
        connection.execute(
            """CREATE INDEX IF NOT EXISTS idx_web_collection_jobs_formatter_profile
               ON web_collection_jobs(workspace_id,formatter_profile_id)
               WHERE formatter_profile_id IS NOT NULL"""
        )
    verify_web_collection_schema(database)
    return database


def verify_web_collection_schema(path: str | Path) -> None:
    verify_database(path)
    with connect(path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        if missing := REQUIRED_TABLES - tables:
            raise RuntimeError(f"Web Collection schema missing tables: {', '.join(sorted(missing))}")
        if missing := REQUIRED_INDEXES - indexes:
            raise RuntimeError(f"Web Collection schema missing indexes: {', '.join(sorted(missing))}")
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(web_collection_jobs)")
        }
        if missing := REQUIRED_JOB_COLUMNS - columns:
            raise RuntimeError(
                f"Web Collection schema missing columns: {', '.join(sorted(missing))}"
            )
