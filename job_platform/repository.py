from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from .database import connect, transaction
from .errors import JobConflict, JobNotFound, JobValidationError, LeaseLost
from .handlers import JobOutcome, JobTypeRegistry
from .schedules import ScheduleCalculator, ensure_utc
from .schemas import JobSchedule, JobStatus, RunStatus, ScheduleKind, TriggerKind


def utc_now() -> datetime:
    return datetime.now(UTC)


def timestamp(value: datetime) -> str:
    return ensure_utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class JobRepository:
    def __init__(
        self,
        database_path: str | Path,
        registry: JobTypeRegistry,
        *,
        calculator: ScheduleCalculator | None = None,
        lease_seconds: int = 60,
    ) -> None:
        self.database_path = Path(database_path)
        self.registry = registry
        self.calculator = calculator or ScheduleCalculator()
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self.lease_seconds = lease_seconds

    def create_job(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        name: str,
        job_type: str,
        schedule: JobSchedule,
        type_config: object,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        clean_name = str(name).strip()
        if not 1 <= len(clean_name) <= 120:
            raise JobValidationError("job name must contain 1-120 characters")
        self.calculator.validate(schedule)
        adapter = self.registry.get(job_type)
        config = adapter.validate_config(type_config)
        instant = now or utc_now()
        job_id = str(uuid4())
        created_at = timestamp(instant)
        with transaction(self.database_path, immediate=True) as connection:
            connection.execute(
                """INSERT INTO jobs (
                    id, workspace_id, name, job_type, status,
                    schedule_kind, schedule_expression, timezone, next_run_at,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'DRAFT', ?, ?, ?, NULL, ?, ?, ?, ?)""",
                (
                    job_id,
                    workspace_id,
                    clean_name,
                    job_type,
                    schedule.kind.value,
                    schedule.expression,
                    schedule.timezone,
                    actor_id,
                    actor_id,
                    created_at,
                    created_at,
                ),
            )
            job = self._required_job(connection, workspace_id, job_id)
            adapter.create_type_row(connection, job, config)
            return job

    def get_job(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        with connect(self.database_path) as connection:
            return self._required_job(connection, workspace_id, job_id)

    def list_jobs(self, workspace_id: str) -> list[dict[str, Any]]:
        with connect(self.database_path) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM jobs WHERE workspace_id=? ORDER BY updated_at DESC, id",
                    (workspace_id,),
                )
            ]

    def get_job_detail(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        with connect(self.database_path) as connection:
            job = self._required_job(connection, workspace_id, job_id)
            type_config = self.registry.get(job["job_type"]).project_job_detail(connection, job)
            return {**job, "type_config": type_config}

    def update_definition(
        self,
        *,
        workspace_id: str,
        job_id: str,
        actor_id: str,
        name: str,
        schedule: JobSchedule,
        type_config: object,
        expected_revision: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        clean_name = str(name).strip()
        if not 1 <= len(clean_name) <= 120:
            raise JobValidationError("job name must contain 1-120 characters")
        self.calculator.validate(schedule)
        instant = timestamp(now or utc_now())
        with transaction(self.database_path, immediate=True) as connection:
            current = self._required_job(connection, workspace_id, job_id)
            if current["status"] == JobStatus.ARCHIVED:
                raise JobConflict("archived jobs cannot be edited")
            if current["definition_revision"] != expected_revision:
                raise JobConflict("job definition changed concurrently")
            adapter = self.registry.get(current["job_type"])
            config = adapter.validate_config(type_config)
            updated = connection.execute(
                """UPDATE jobs SET name=?, schedule_kind=?, schedule_expression=?, timezone=?,
                    next_run_at=NULL, status=CASE WHEN status='ACTIVE' THEN 'PAUSED' ELSE status END,
                    definition_revision=definition_revision+1,
                    last_tested_revision=NULL, last_tested_at=NULL,
                    updated_by=?, updated_at=?
                    WHERE workspace_id=? AND id=? AND definition_revision=?""",
                (
                    clean_name,
                    schedule.kind.value,
                    schedule.expression,
                    schedule.timezone,
                    actor_id,
                    instant,
                    workspace_id,
                    job_id,
                    expected_revision,
                ),
            ).rowcount
            if updated != 1:
                raise JobConflict("job definition changed concurrently")
            job = self._required_job(connection, workspace_id, job_id)
            adapter.update_type_row(connection, job, config)
            return job

    def mark_tested(
        self,
        workspace_id: str,
        job_id: str,
        revision: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        tested_at = timestamp(now or utc_now())
        with transaction(self.database_path, immediate=True) as connection:
            return connection.execute(
                """UPDATE jobs SET last_tested_revision=?, last_tested_at=?, updated_at=?
                   WHERE workspace_id=? AND id=? AND definition_revision=? AND status!='ARCHIVED'""",
                (revision, tested_at, tested_at, workspace_id, job_id, revision),
            ).rowcount == 1

    def activate(
        self,
        workspace_id: str,
        job_id: str,
        actor_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        instant = now or utc_now()
        with transaction(self.database_path, immediate=True) as connection:
            job = self._required_job(connection, workspace_id, job_id)
            if job["status"] == JobStatus.ARCHIVED:
                raise JobConflict("archived jobs cannot be activated")
            if job["last_tested_revision"] != job["definition_revision"]:
                raise JobConflict("current job definition has not passed its test")
            schedule = self._schedule(job)
            next_run = (
                None
                if schedule.kind is ScheduleKind.MANUAL
                else timestamp(self.calculator.next_after(schedule, instant))
            )
            updated_at = timestamp(instant)
            connection.execute(
                """UPDATE jobs SET status='ACTIVE', next_run_at=?, consecutive_failures=0,
                    updated_by=?, updated_at=? WHERE workspace_id=? AND id=?""",
                (next_run, actor_id, updated_at, workspace_id, job_id),
            )
            return self._required_job(connection, workspace_id, job_id)

    def pause(self, workspace_id: str, job_id: str, actor_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        with transaction(self.database_path, immediate=True) as connection:
            job = self._required_job(connection, workspace_id, job_id)
            if job["status"] == JobStatus.ARCHIVED:
                raise JobConflict("archived jobs cannot be paused")
            connection.execute(
                "UPDATE jobs SET status='PAUSED', next_run_at=NULL, updated_by=?, updated_at=? WHERE workspace_id=? AND id=?",
                (actor_id, timestamp(now or utc_now()), workspace_id, job_id),
            )
            return self._required_job(connection, workspace_id, job_id)

    def archive(self, workspace_id: str, job_id: str, actor_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        instant = timestamp(now or utc_now())
        with transaction(self.database_path, immediate=True) as connection:
            job = self._required_job(connection, workspace_id, job_id)
            if job["status"] == JobStatus.ARCHIVED:
                return job
            running = connection.execute(
                "SELECT 1 FROM job_runs WHERE job_id=? AND status='RUNNING'", (job_id,)
            ).fetchone()
            if running:
                raise JobConflict("running jobs cannot be archived")
            connection.execute(
                """UPDATE job_runs SET status='CANCELLED',finished_at=?,outcome_code='JOB_ARCHIVED'
                   WHERE job_id=? AND status='QUEUED'""",
                (instant, job_id),
            )
            connection.execute(
                """UPDATE jobs SET status='ARCHIVED',next_run_at=NULL,updated_by=?,updated_at=?
                   WHERE workspace_id=? AND id=?""",
                (actor_id, instant, workspace_id, job_id),
            )
            return self._required_job(connection, workspace_id, job_id)

    def enqueue_manual(
        self, workspace_id: str, job_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        instant = now or utc_now()
        with transaction(self.database_path, immediate=True) as connection:
            job = self._required_job(connection, workspace_id, job_id)
            if job["status"] != JobStatus.ACTIVE:
                raise JobConflict("only active jobs can run")
            try:
                return self._insert_run(
                    connection,
                    job,
                    trigger=TriggerKind.MANUAL,
                    dedupe_key=f"manual:{uuid4()}",
                    scheduled_for=instant,
                )
            except sqlite3.IntegrityError as error:
                active = connection.execute(
                    "SELECT 1 FROM job_runs WHERE job_id=? AND status IN ('QUEUED','RUNNING')",
                    (job_id,),
                ).fetchone()
                if active:
                    raise JobConflict("job already has an active run") from error
                raise

    def enqueue_due(self, *, now: datetime | None = None, limit: int = 50) -> list[dict[str, Any]]:
        instant = now or utc_now()
        now_value = timestamp(instant)
        queued: list[dict[str, Any]] = []
        with transaction(self.database_path, immediate=True) as connection:
            jobs = connection.execute(
                """SELECT * FROM jobs WHERE status='ACTIVE' AND next_run_at IS NOT NULL
                   AND next_run_at<=? ORDER BY next_run_at,id LIMIT ?""",
                (now_value, min(max(limit, 1), 50)),
            ).fetchall()
            for row in jobs:
                job = dict(row)
                scheduled_for = parse_timestamp(job["next_run_at"])
                try:
                    queued.append(
                        self._insert_run(
                            connection,
                            job,
                            trigger=TriggerKind.SCHEDULED,
                            dedupe_key=f"schedule:{timestamp(scheduled_for)}",
                            scheduled_for=scheduled_for,
                        )
                    )
                except sqlite3.IntegrityError:
                    pass
                schedule = self._schedule(job)
                candidate = self.calculator.next_after(schedule, scheduled_for)
                hops = 0
                while candidate <= instant and hops < 10_000:
                    candidate = self.calculator.next_after(schedule, candidate)
                    hops += 1
                if candidate <= instant:
                    raise JobValidationError("schedule catch-up exceeded bounded occurrences")
                connection.execute(
                    "UPDATE jobs SET next_run_at=?, updated_at=? WHERE id=? AND workspace_id=?",
                    (timestamp(candidate), now_value, job["id"], job["workspace_id"]),
                )
        return queued

    def claim_next(self, worker_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
        instant = now or utc_now()
        now_value = timestamp(instant)
        with transaction(self.database_path, immediate=True) as connection:
            while True:
                row = connection.execute(
                    """SELECT r.*, j.definition_revision AS current_revision
                       FROM job_runs r JOIN jobs j ON j.id=r.job_id AND j.workspace_id=r.workspace_id
                       WHERE r.status='QUEUED' AND r.scheduled_for<=?
                       ORDER BY r.scheduled_for,r.id LIMIT 1""",
                    (now_value,),
                ).fetchone()
                if row is None:
                    return None
                run = dict(row)
                if run["definition_revision"] != run["current_revision"]:
                    connection.execute(
                        """UPDATE job_runs SET status='CANCELLED', finished_at=?,
                           outcome_code='STALE_DEFINITION' WHERE id=? AND status='QUEUED'""",
                        (now_value, run["id"]),
                    )
                    continue
                token = str(uuid4())
                lease_until = timestamp(instant + timedelta(seconds=self.lease_seconds))
                if connection.execute(
                    """UPDATE job_runs SET status='RUNNING',worker_id=?,lease_token=?,lease_until=?,
                       started_at=?,heartbeat_at=? WHERE id=? AND status='QUEUED'""",
                    (worker_id, token, lease_until, now_value, now_value, run["id"]),
                ).rowcount != 1:
                    continue
                claimed = connection.execute("SELECT * FROM job_runs WHERE id=?", (run["id"],)).fetchone()
                return dict(claimed)

    def heartbeat(self, run_id: str, lease_token: str, *, now: datetime | None = None) -> None:
        instant = now or utc_now()
        with transaction(self.database_path, immediate=True) as connection:
            heartbeat_at = timestamp(instant)
            lease_until = timestamp(instant + timedelta(seconds=self.lease_seconds))
            if connection.execute(
                """UPDATE job_runs SET
                   heartbeat_at=CASE WHEN heartbeat_at>? THEN heartbeat_at ELSE ? END,
                   lease_until=CASE WHEN lease_until>? THEN lease_until ELSE ? END
                   WHERE id=? AND status='RUNNING' AND lease_token=? AND lease_until>?""",
                (
                    heartbeat_at,
                    heartbeat_at,
                    lease_until,
                    lease_until,
                    run_id,
                    lease_token,
                    heartbeat_at,
                ),
            ).rowcount != 1:
                raise LeaseLost("job run lease was lost")

    def complete(
        self,
        run_id: str,
        lease_token: str,
        outcome: JobOutcome,
        *,
        error_code: str | None = None,
        error_message: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if error_code and len(error_code) > 64:
            raise JobValidationError("error code is too long")
        clean_message = (error_message or "")[:500] or None
        instant = timestamp(now or utc_now())
        with transaction(self.database_path, immediate=True) as connection:
            run = connection.execute(
                "SELECT * FROM job_runs WHERE id=? AND status='RUNNING' AND lease_token=? AND lease_until>?",
                (run_id, lease_token, instant),
            ).fetchone()
            if run is None:
                raise LeaseLost("job run lease was lost")
            failed = outcome.status in {RunStatus.FAILED, RunStatus.PARTIAL}
            connection.execute(
                """UPDATE job_runs SET status=?,lease_token=NULL,lease_until=NULL,finished_at=?,
                   outcome_code=?,error_code=?,error_message=? WHERE id=? AND lease_token=?""",
                (
                    outcome.status.value,
                    instant,
                    outcome.outcome_code,
                    error_code if failed else None,
                    clean_message if failed else None,
                    run_id,
                    lease_token,
                ),
            )
            job = self._required_job(connection, run["workspace_id"], run["job_id"])
            failures = job["consecutive_failures"] + 1 if failed else 0
            status = "PAUSED" if failed and failures >= job["max_consecutive_failures"] else job["status"]
            connection.execute(
                """UPDATE jobs SET status=?,consecutive_failures=?,last_run_at=?,
                   last_success_at=CASE WHEN ? THEN last_success_at ELSE ? END,
                   next_run_at=CASE WHEN ?='PAUSED' THEN NULL ELSE next_run_at END,
                   updated_at=? WHERE workspace_id=? AND id=?""",
                (status, failures, instant, int(failed), instant, status, instant, run["workspace_id"], run["job_id"]),
            )
            return dict(connection.execute("SELECT * FROM job_runs WHERE id=?", (run_id,)).fetchone())

    def recover_stale(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        instant = now or utc_now()
        now_value = timestamp(instant)
        recovered: list[dict[str, Any]] = []
        with transaction(self.database_path, immediate=True) as connection:
            rows = connection.execute(
                "SELECT * FROM job_runs WHERE status='RUNNING' AND lease_until<=? ORDER BY lease_until,id",
                (now_value,),
            ).fetchall()
            for row in rows:
                run = dict(row)
                connection.execute(
                    """UPDATE job_runs SET status='FAILED',lease_token=NULL,lease_until=NULL,
                       finished_at=?,outcome_code='WORKER_LOST',error_code='WORKER_LOST',
                       error_message='Worker lease expired' WHERE id=? AND status='RUNNING'""",
                    (now_value, run["id"]),
                )
                job = self._required_job(connection, run["workspace_id"], run["job_id"])
                failures = job["consecutive_failures"] + 1
                paused = failures >= job["max_consecutive_failures"]
                connection.execute(
                    """UPDATE jobs SET consecutive_failures=?,status=CASE WHEN ? THEN 'PAUSED' ELSE status END,
                       next_run_at=CASE WHEN ? THEN NULL ELSE next_run_at END,last_run_at=?,updated_at=?
                       WHERE workspace_id=? AND id=?""",
                    (failures, int(paused), int(paused), now_value, now_value, run["workspace_id"], run["job_id"]),
                )
                recovered.append(run)
                if not paused and job["status"] == JobStatus.ACTIVE and run["definition_revision"] == job["definition_revision"]:
                    self._insert_run(
                        connection,
                        job,
                        trigger=TriggerKind.RETRY,
                        dedupe_key=f"retry:{run['id']}:{run['attempt'] + 1}",
                        scheduled_for=instant,
                        source_run_id=run["id"],
                        attempt=run["attempt"] + 1,
                    )
        return recovered

    def list_runs(self, workspace_id: str, job_id: str) -> list[dict[str, Any]]:
        with connect(self.database_path) as connection:
            self._required_job(connection, workspace_id, job_id)
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM job_runs WHERE workspace_id=? AND job_id=? ORDER BY created_at DESC,id",
                    (workspace_id, job_id),
                )
            ]

    def list_run_details(self, workspace_id: str, job_id: str) -> list[dict[str, Any]]:
        with connect(self.database_path) as connection:
            job = self._required_job(connection, workspace_id, job_id)
            adapter = self.registry.get(job["job_type"])
            runs = connection.execute(
                "SELECT * FROM job_runs WHERE workspace_id=? AND job_id=? ORDER BY created_at DESC,id",
                (workspace_id, job_id),
            ).fetchall()
            return [
                {**dict(run), "type_result": adapter.project_run_detail(connection, dict(run))}
                for run in runs
            ]

    def _insert_run(
        self,
        connection: sqlite3.Connection,
        job: dict[str, Any],
        *,
        trigger: TriggerKind,
        dedupe_key: str,
        scheduled_for: datetime,
        source_run_id: str | None = None,
        attempt: int = 1,
    ) -> dict[str, Any]:
        run_id = str(uuid4())
        created_at = timestamp(utc_now())
        connection.execute(
            """INSERT INTO job_runs (
                id,workspace_id,job_id,trigger_kind,dedupe_key,scheduled_for,source_run_id,
                status,attempt,definition_revision,created_at
            ) VALUES (?,?,?,?,?,?,?,'QUEUED',?,?,?)""",
            (
                run_id,
                job["workspace_id"],
                job["id"],
                trigger.value,
                dedupe_key,
                timestamp(scheduled_for),
                source_run_id,
                attempt,
                job["definition_revision"],
                created_at,
            ),
        )
        return dict(connection.execute("SELECT * FROM job_runs WHERE id=?", (run_id,)).fetchone())

    @staticmethod
    def _required_job(connection: sqlite3.Connection, workspace_id: str, job_id: str) -> dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM jobs WHERE workspace_id=? AND id=?", (workspace_id, job_id)
        ).fetchone()
        if row is None:
            raise JobNotFound("job not found")
        return dict(row)

    @staticmethod
    def _schedule(job: dict[str, Any]) -> JobSchedule:
        return JobSchedule(
            ScheduleKind(job["schedule_kind"]),
            job["schedule_expression"],
            job["timezone"],
        )
