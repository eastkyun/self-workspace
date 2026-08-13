from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .database import connect
from .repository import timestamp


def health_snapshot(database_path: str | Path, *, now: datetime | None = None) -> dict[str, object]:
    instant = now or datetime.now(UTC)
    with connect(database_path) as connection:
        queue = connection.execute(
            """SELECT COUNT(*) AS depth, MIN(scheduled_for) AS oldest
               FROM job_runs WHERE status='QUEUED'"""
        ).fetchone()
        worker = connection.execute(
            """SELECT heartbeat_at,status FROM job_worker_invocations
               ORDER BY heartbeat_at DESC LIMIT 1"""
        ).fetchone()
        types = [row[0] for row in connection.execute("SELECT DISTINCT job_type FROM jobs ORDER BY job_type")]
    return {
        "status": "ok",
        "checked_at": timestamp(instant),
        "queue": {"depth": queue["depth"], "oldest_scheduled_for": queue["oldest"]},
        "worker": (
            {"status": worker["status"], "last_heartbeat_at": worker["heartbeat_at"]}
            if worker else {"status": "unknown", "last_heartbeat_at": None}
        ),
        "registered_persisted_types": types,
    }
