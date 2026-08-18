from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from datetime import UTC, datetime

from .database import connect
from .errors import LeaseLost
from .handlers import JobOutcome, JobTestOutcome, JobTypeRegistry
from .repository import JobRepository, parse_timestamp
from .schemas import JobExecutionContext, RunStatus, TriggerKind

ExecutionAuthorizer = Callable[[str, str, str], bool]


class JobWorker:
    """Single-process worker loop with durable claims and lease fencing."""

    def __init__(
        self,
        repository: JobRepository,
        registry: JobTypeRegistry,
        *,
        worker_id: str,
        authorize_execution: ExecutionAuthorizer,
        heartbeat_seconds: int = 20,
    ) -> None:
        self.repository = repository
        self.registry = registry
        self.worker_id = worker_id
        self.authorize_execution = authorize_execution
        if heartbeat_seconds < 1 or heartbeat_seconds >= repository.lease_seconds:
            raise ValueError("heartbeat must be positive and shorter than the lease")
        self.heartbeat_seconds = heartbeat_seconds

    async def run_due(self, *, now: datetime | None = None, limit: int = 50) -> dict[str, int]:
        fixed_now = now is not None
        instant = (now or datetime.now(UTC)).astimezone(UTC)
        recovered = len(self.repository.recover_stale(now=instant))
        enqueued = len(self.repository.enqueue_due(now=instant, limit=limit))
        claimed = completed = failed = 0
        while claimed < limit:
            claim_time = instant if fixed_now else datetime.now(UTC)
            run = self.repository.claim_next(self.worker_id, now=claim_time)
            if run is None:
                break
            claimed += 1
            outcome = await self._execute(run, now=instant if fixed_now else None)
            if outcome.status in {RunStatus.FAILED, RunStatus.PARTIAL}:
                failed += 1
            else:
                completed += 1
        return {
            "recovered": recovered,
            "enqueued": enqueued,
            "claimed": claimed,
            "completed": completed,
            "failed": failed,
        }

    async def _execute(self, run: dict, *, now: datetime | None) -> JobOutcome:
        job = self.repository.get_job(run["workspace_id"], run["job_id"])
        token = run["lease_token"]
        is_test = run["dedupe_key"].startswith("test:")
        try:
            allowed = self.authorize_execution(
                job["workspace_id"], job["created_by"], job["job_type"]
            )
            if inspect.isawaitable(allowed):
                allowed = await allowed
            if not allowed:
                outcome = JobOutcome(RunStatus.FAILED, "AUTHORIZATION_REVOKED")
                self.repository.complete(
                    run["id"], token, outcome,
                    error_code="AUTHORIZATION_REVOKED",
                    error_message="Execution authorization is no longer available",
                    affect_job_health=not is_test,
                    now=now,
                )
                return outcome
            context = JobExecutionContext(
                run_id=run["id"],
                job_id=job["id"],
                workspace_id=job["workspace_id"],
                actor_id=job["created_by"],
                trigger_kind=TriggerKind(run["trigger_kind"]),
                scheduled_for=parse_timestamp(run["scheduled_for"]),
            )
            adapter = self.registry.get(job["job_type"])
            if is_test:
                with connect(self.repository.database_path) as connection:
                    config = adapter.load_type_config(connection, job)
                operation = adapter.test(context, config)
            elif context.trigger_kind is TriggerKind.RETRY:
                operation = adapter.retry(context, run["source_run_id"])
            else:
                operation = adapter.execute(context)
            task = asyncio.create_task(operation)
            heartbeat_interval = min(
                self.heartbeat_seconds, self.repository.lease_seconds / 3
            )
            try:
                while True:
                    done, _ = await asyncio.wait({task}, timeout=heartbeat_interval)
                    if task in done:
                        result = task.result()
                        break
                    self.repository.heartbeat(run["id"], token)
            except BaseException:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
            if is_test:
                if not isinstance(result, JobTestOutcome):
                    raise RuntimeError("Job Type returned an invalid test outcome")
                if result.success:
                    tested = self.repository.mark_tested(
                        job["workspace_id"],
                        job["id"],
                        run["definition_revision"],
                        now=now,
                    )
                    outcome = JobOutcome(
                        RunStatus.SUCCEEDED if tested else RunStatus.FAILED,
                        result.code if tested else "STALE_DEFINITION",
                    )
                else:
                    outcome = JobOutcome(RunStatus.FAILED, result.code)
            else:
                if not isinstance(result, JobOutcome):
                    raise RuntimeError("Job Type returned an invalid execution outcome")
                outcome = result
            failed = outcome.status in {RunStatus.FAILED, RunStatus.PARTIAL}
            self.repository.complete(
                run["id"],
                token,
                outcome,
                error_code=outcome.outcome_code if failed else None,
                error_message="Job handler reported a failure" if failed else None,
                affect_job_health=not is_test,
                now=now,
            )
            return outcome
        except LeaseLost:
            raise
        except Exception:
            outcome = JobOutcome(RunStatus.FAILED, "HANDLER_FAILED")
            self.repository.complete(
                run["id"], token, outcome,
                error_code="HANDLER_FAILED",
                error_message="Job handler failed",
                affect_job_health=not is_test,
                now=now,
            )
            return outcome
