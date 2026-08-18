from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from .database import connect
from .repository import parse_timestamp, timestamp


def health_snapshot(
    database_path: str | Path,
    *,
    now: datetime | None = None,
    stale_after_seconds: int = 180,
) -> dict[str, object]:
    instant = now or datetime.now(UTC)
    with connect(database_path) as connection:
        queue = connection.execute(
            """SELECT COUNT(*) AS depth, MIN(scheduled_for) AS oldest
               FROM job_runs WHERE status='QUEUED'"""
        ).fetchone()
        worker = connection.execute(
            """SELECT heartbeat_at,status,version FROM job_worker_invocations
               ORDER BY CASE WHEN status='RUNNING' THEN 0 ELSE 1 END,
                        CAST(version AS INTEGER) DESC, heartbeat_at DESC LIMIT 1"""
        ).fetchone()
        types = [row[0] for row in connection.execute("SELECT DISTINCT job_type FROM jobs ORDER BY job_type")]
    if worker is None:
        worker_health = {
            "availability": "unknown",
            "status": "unknown",
            "last_heartbeat_at": None,
            "version": None,
        }
    else:
        age_seconds = max(
            0, int((instant - parse_timestamp(worker["heartbeat_at"])).total_seconds())
        )
        worker_status = worker["status"]
        if worker_status == "FAILED":
            availability = "failed"
        elif worker_status in {"STOPPED", "SUCCEEDED"}:
            availability = "stopped"
        elif age_seconds > stale_after_seconds:
            availability = "stale"
        else:
            availability = "healthy"
        worker_health = {
            "availability": availability,
            "status": worker_status,
            "last_heartbeat_at": worker["heartbeat_at"],
            "version": worker["version"],
        }
    return {
        "status": "ok",
        "checked_at": timestamp(instant),
        "queue": {"depth": queue["depth"], "oldest_scheduled_for": queue["oldest"]},
        "worker": worker_health,
        "registered_persisted_types": types,
    }
