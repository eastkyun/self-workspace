from __future__ import annotations

from datetime import UTC, datetime, timedelta

from job_platform.database import connect, initialize_database
from job_platform.health import health_snapshot
from job_platform.repository import timestamp


def insert_worker(database, *, status: str, heartbeat_at: datetime) -> None:
    finished_at = None if status == "RUNNING" else timestamp(heartbeat_at)
    with connect(database) as connection:
        connection.execute(
            """INSERT INTO job_worker_invocations(
              id,worker_id,version,started_at,heartbeat_at,finished_at,status
            ) VALUES (?,?,?,?,?,?,?)""",
            (
                f"invocation-{status}",
                "worker-a",
                "1",
                timestamp(heartbeat_at),
                timestamp(heartbeat_at),
                finished_at,
                status,
            ),
        )


def test_health_classifies_worker_freshness_without_sensitive_details(tmp_path):
    database = initialize_database(tmp_path / "jobs.db")
    now = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
    insert_worker(database, status="RUNNING", heartbeat_at=now - timedelta(seconds=20))

    healthy = health_snapshot(database, now=now, stale_after_seconds=60)
    assert healthy["worker"]["availability"] == "healthy"
    assert healthy["worker"]["status"] == "RUNNING"
    assert "database" not in healthy
    assert "path" not in str(healthy)

    stale = health_snapshot(database, now=now + timedelta(seconds=61), stale_after_seconds=60)
    assert stale["worker"]["availability"] == "stale"


def test_health_preserves_failed_and_stopped_worker_states(tmp_path):
    now = datetime(2026, 8, 14, 0, 0, tzinfo=UTC)
    failed_database = initialize_database(tmp_path / "failed.db")
    insert_worker(failed_database, status="FAILED", heartbeat_at=now)
    assert health_snapshot(failed_database, now=now)["worker"]["availability"] == "failed"

    stopped_database = initialize_database(tmp_path / "stopped.db")
    insert_worker(stopped_database, status="STOPPED", heartbeat_at=now)
    assert health_snapshot(stopped_database, now=now)["worker"]["availability"] == "stopped"

    one_shot_database = initialize_database(tmp_path / "one-shot.db")
    insert_worker(one_shot_database, status="SUCCEEDED", heartbeat_at=now)
    assert health_snapshot(one_shot_database, now=now)["worker"]["availability"] == "stopped"
