from __future__ import annotations

import sqlite3
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from job_platform import (
    JobOutcome,
    JobRepository,
    JobSchedule,
    JobTypeDescriptor,
    JobTypeRegistry,
    RunStatus,
    ScheduleCalculator,
    ScheduleKind,
    initialize_database,
    verify_database,
)
from job_platform.database import connect
from job_platform.errors import JobConflict, JobValidationError, LeaseLost
from job_platform.service import JobWorker
from job_platform.runtime import create_job_runtime
from shared.config import load_config


@dataclass(frozen=True)
class FakeConfig:
    value: str


class FakeAdapter:
    descriptor = JobTypeDescriptor(
        key="FAKE",
        label="Fake",
        description="Test adapter",
        icon="/fake.svg",
        editor_key="fake",
    )

    def __init__(self, delay: float = 0) -> None:
        self.delay = delay

    def validate_config(self, payload):
        if not isinstance(payload, dict) or not str(payload.get("value", "")).strip():
            raise JobValidationError("fake value is required")
        return FakeConfig(str(payload["value"]).strip())

    def create_type_row(self, connection, job, config):
        connection.execute(
            "CREATE TABLE IF NOT EXISTS fake_jobs (job_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, value TEXT NOT NULL, FOREIGN KEY(workspace_id,job_id) REFERENCES jobs(workspace_id,id) ON DELETE CASCADE)"
        )
        connection.execute(
            "INSERT INTO fake_jobs(job_id,workspace_id,value) VALUES (?,?,?)",
            (job["id"], job["workspace_id"], config.value),
        )

    def update_type_row(self, connection, job, config):
        connection.execute("UPDATE fake_jobs SET value=? WHERE job_id=? AND workspace_id=?", (config.value, job["id"], job["workspace_id"]))

    def load_type_config(self, connection, job):
        return connection.execute("SELECT value FROM fake_jobs WHERE job_id=?", (job["id"],)).fetchone()[0]

    def project_job_detail(self, connection, job):
        return {"value": self.load_type_config(connection, job)}

    def project_run_detail(self, connection, run):
        return {}

    async def execute(self, context):
        if self.delay:
            await asyncio.sleep(self.delay)
        return JobOutcome(RunStatus.SUCCEEDED, "FAKE_OK")

    async def retry(self, context, source_run_id):
        return JobOutcome(RunStatus.SUCCEEDED, "FAKE_RETRY_OK")


@pytest.fixture()
def repository(tmp_path):
    database = initialize_database(tmp_path / "jobs.db")
    return JobRepository(database, JobTypeRegistry([FakeAdapter()]))


def create_tested_job(repository, *, schedule=None, now=None, workspace="workspace-a", failures=5):
    instant = now or datetime(2026, 8, 13, tzinfo=UTC)
    job = repository.create_job(
        workspace_id=workspace,
        actor_id="user-a",
        name="Collect",
        job_type="FAKE",
        schedule=schedule or JobSchedule(ScheduleKind.MANUAL),
        type_config={"value": "v1"},
        now=instant,
    )
    if failures != 5:
        with connect(repository.database_path) as connection:
            connection.execute("UPDATE jobs SET max_consecutive_failures=? WHERE id=?", (failures, job["id"]))
    assert repository.mark_tested(workspace, job["id"], 1, now=instant)
    return repository.activate(workspace, job["id"], "user-a", now=instant)


def test_schema_is_versioned_and_tenant_foreign_keys_reject_mismatch(tmp_path):
    database = initialize_database(tmp_path / "jobs.db")
    verify_database(database)
    with connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """INSERT INTO job_runs(id,workspace_id,job_id,trigger_kind,dedupe_key,scheduled_for,status,attempt,definition_revision,created_at)
                VALUES ('r','wrong','missing','MANUAL','x','2026-01-01T00:00:00Z','QUEUED',1,1,'2026-01-01T00:00:00Z')"""
            )


def test_schedule_validation_interval_and_dst_boundaries():
    calculator = ScheduleCalculator()
    with pytest.raises(JobValidationError):
        calculator.validate(JobSchedule(ScheduleKind.INTERVAL, "299"))
    assert calculator.next_after(
        JobSchedule(ScheduleKind.INTERVAL, "300"), datetime(2026, 1, 1, tzinfo=UTC)
    ) == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)

    spring = calculator.next_after(
        JobSchedule(ScheduleKind.CRON, "30 2 * * *", "America/New_York"),
        datetime(2026, 3, 8, 5, 0, tzinfo=UTC),
    )
    assert spring == datetime(2026, 3, 9, 6, 30, tzinfo=UTC)
    autumn = calculator.next_after(
        JobSchedule(ScheduleKind.CRON, "30 1 * * *", "America/New_York"),
        datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
    )
    assert autumn == datetime(2026, 11, 2, 6, 30, tzinfo=UTC)


def test_definition_revision_test_gate_and_type_is_immutable(repository):
    job = repository.create_job(
        workspace_id="workspace-a",
        actor_id="user-a",
        name="Draft",
        job_type="FAKE",
        schedule=JobSchedule(ScheduleKind.MANUAL),
        type_config={"value": "v1"},
    )
    with pytest.raises(JobConflict, match="has not passed"):
        repository.activate("workspace-a", job["id"], "user-a")
    assert repository.mark_tested("workspace-a", job["id"], 1)
    active = repository.activate("workspace-a", job["id"], "user-a")
    assert active["status"] == "ACTIVE"

    updated = repository.update_definition(
        workspace_id="workspace-a",
        job_id=job["id"],
        actor_id="user-a",
        name="Changed",
        schedule=JobSchedule(ScheduleKind.INTERVAL, "300"),
        type_config={"value": "v2"},
        expected_revision=1,
    )
    assert updated["definition_revision"] == 2
    assert updated["last_tested_revision"] is None
    assert updated["status"] == "PAUSED"
    with pytest.raises(JobConflict):
        repository.update_definition(
            workspace_id="workspace-a", job_id=job["id"], actor_id="user-a",
            name="stale", schedule=JobSchedule(ScheduleKind.MANUAL),
            type_config={"value": "v3"}, expected_revision=1,
        )


def test_due_enqueue_is_deduplicated_and_coalesces_missed_occurrences(repository):
    start = datetime(2026, 8, 13, 0, 0, tzinfo=UTC)
    job = create_tested_job(
        repository,
        schedule=JobSchedule(ScheduleKind.INTERVAL, "300"),
        now=start,
    )
    due = start + timedelta(minutes=21)
    first = repository.enqueue_due(now=due)
    second = repository.enqueue_due(now=due)
    assert len(first) == 1
    assert second == []
    runs = repository.list_runs("workspace-a", job["id"])
    assert len(runs) == 1
    assert repository.get_job("workspace-a", job["id"])["next_run_at"] == "2026-08-13T00:25:00Z"


def test_overlapping_enqueuers_and_claimers_produce_one_active_run(repository):
    start = datetime(2026, 8, 13, tzinfo=UTC)
    job = create_tested_job(
        repository, schedule=JobSchedule(ScheduleKind.INTERVAL, "300"), now=start
    )
    due = start + timedelta(minutes=5)
    with ThreadPoolExecutor(max_workers=2) as pool:
        enqueue_results = list(pool.map(lambda _: repository.enqueue_due(now=due), range(2)))
    assert sum(len(item) for item in enqueue_results) == 1

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda index: repository.claim_next(f"worker-{index}", now=due), range(2)))
    claimed = [item for item in claims if item is not None]
    assert len(claimed) == 1
    assert claimed[0]["job_id"] == job["id"]


def test_claim_heartbeat_completion_and_failure_pause(repository):
    now = datetime(2026, 8, 13, tzinfo=UTC)
    job = create_tested_job(repository, now=now, failures=1)
    run = repository.enqueue_manual("workspace-a", job["id"], now=now)
    claimed = repository.claim_next("worker-a", now=now)
    assert claimed["id"] == run["id"]
    repository.heartbeat(run["id"], claimed["lease_token"], now=now + timedelta(seconds=10))
    completed = repository.complete(
        run["id"], claimed["lease_token"], JobOutcome(RunStatus.FAILED, "FETCH_FAILED"),
        error_code="FETCH_FAILED", error_message="safe", now=now + timedelta(seconds=20),
    )
    assert completed["status"] == "FAILED"
    assert repository.get_job("workspace-a", job["id"])["status"] == "PAUSED"
    with pytest.raises(LeaseLost):
        repository.complete(run["id"], claimed["lease_token"], JobOutcome(RunStatus.SUCCEEDED, "OK"), now=now + timedelta(seconds=21))


def test_manual_execution_returns_conflict_when_run_is_already_active(repository):
    now = datetime(2026, 8, 13, tzinfo=UTC)
    job = create_tested_job(repository, now=now)
    repository.enqueue_manual("workspace-a", job["id"], now=now)
    with pytest.raises(JobConflict, match="already has an active run"):
        repository.enqueue_manual("workspace-a", job["id"], now=now)


def test_stale_recovery_fences_old_worker_and_creates_retry(repository):
    now = datetime(2026, 8, 13, tzinfo=UTC)
    job = create_tested_job(repository, now=now)
    original = repository.enqueue_manual("workspace-a", job["id"], now=now)
    claimed = repository.claim_next("worker-a", now=now)
    recovered = repository.recover_stale(now=now + timedelta(seconds=61))
    assert [item["id"] for item in recovered] == [original["id"]]
    runs = repository.list_runs("workspace-a", job["id"])
    assert {item["status"] for item in runs} == {"FAILED", "QUEUED"}
    retry = next(item for item in runs if item["status"] == "QUEUED")
    assert retry["source_run_id"] == original["id"]
    with pytest.raises(LeaseLost):
        repository.complete(original["id"], claimed["lease_token"], JobOutcome(RunStatus.SUCCEEDED, "OK"), now=now + timedelta(seconds=62))


def test_registry_rejects_duplicate_type_and_editor_keys():
    first = FakeAdapter()
    second = FakeAdapter()
    with pytest.raises(ValueError, match="duplicate Job Type key"):
        JobTypeRegistry([first, second])


def test_web_and_worker_runtime_share_registry_and_database_contract(tmp_path):
    database = initialize_database(tmp_path / "jobs.db")
    config = load_config({"JOBS_DB_PATH": str(database)})
    registry = JobTypeRegistry([FakeAdapter()])
    web = create_job_runtime(config, registry=registry, mode="web")
    worker = create_job_runtime(
        config,
        registry=registry,
        mode="worker",
        authorize_execution=lambda *_args: True,
        worker_id="worker-test",
    )
    assert web.repository.database_path == worker.repository.database_path
    assert web.registry.descriptors() == worker.registry.descriptors()
    assert web.worker is None
    assert worker.worker is not None
    asyncio.run(web.close())
    asyncio.run(web.close())
    asyncio.run(worker.close())


def test_worker_executes_claimed_run_and_rechecks_authorization(repository):
    now = datetime(2026, 8, 13, tzinfo=UTC)
    job = create_tested_job(repository, now=now)
    repository.enqueue_manual("workspace-a", job["id"], now=now)
    worker = JobWorker(
        repository,
        repository.registry,
        worker_id="worker-a",
        authorize_execution=lambda workspace, actor, job_type: (
            workspace == "workspace-a" and actor == "user-a" and job_type == "FAKE"
        ),
    )
    result = asyncio.run(worker.run_due(now=now))
    assert result["completed"] == 1
    assert repository.list_runs("workspace-a", job["id"])[0]["outcome_code"] == "FAKE_OK"

    second = repository.enqueue_manual("workspace-a", job["id"], now=now + timedelta(minutes=1))
    denied = JobWorker(
        repository,
        repository.registry,
        worker_id="worker-b",
        authorize_execution=lambda *_args: False,
    )
    result = asyncio.run(denied.run_due(now=now + timedelta(minutes=1)))
    assert result["failed"] == 1
    run = next(item for item in repository.list_runs("workspace-a", job["id"]) if item["id"] == second["id"])
    assert run["error_code"] == "AUTHORIZATION_REVOKED"


def test_worker_heartbeats_during_long_async_handler(tmp_path):
    database = initialize_database(tmp_path / "jobs.db")
    adapter = FakeAdapter(delay=1.2)
    registry = JobTypeRegistry([adapter])
    repository = JobRepository(database, registry, lease_seconds=2)
    job = create_tested_job(repository)
    repository.enqueue_manual("workspace-a", job["id"])
    worker = JobWorker(
        repository,
        registry,
        worker_id="worker-heartbeat",
        authorize_execution=lambda *_args: True,
        heartbeat_seconds=1,
    )
    result = asyncio.run(worker.run_due())
    assert result["completed"] == 1
    run = repository.list_runs("workspace-a", job["id"])[0]
    assert run["heartbeat_at"] >= run["started_at"]
