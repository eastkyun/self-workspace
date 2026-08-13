from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Literal
from uuid import uuid4

from shared.config import AppConfig

from .database import connect, verify_database
from .handlers import JobTypeRegistry
from .repository import JobRepository
from .schedules import ScheduleCalculator
from .service import ExecutionAuthorizer, JobWorker


@dataclass(slots=True)
class JobRuntime:
    mode: Literal["web", "worker"]
    registry: JobTypeRegistry
    repository: JobRepository
    worker: JobWorker | None
    _closed: bool = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for descriptor in self.registry.descriptors():
            adapter = self.registry.get(descriptor.key)
            close = getattr(adapter, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result


def create_job_runtime(
    configuration: AppConfig,
    *,
    registry: JobTypeRegistry,
    mode: Literal["web", "worker"],
    authorize_execution: ExecutionAuthorizer | None = None,
    worker_id: str | None = None,
) -> JobRuntime:
    """Build web and worker runtimes from the same verified core boundary.

    Database creation/migration is intentionally excluded. Operators must run
    the explicit initialization/upgrade command before either runtime starts.
    """
    verify_database(configuration.databases.jobs)
    with connect(configuration.databases.jobs) as connection:
        registry.verify_persisted_types(connection)
    repository = JobRepository(
        configuration.databases.jobs,
        registry,
        calculator=ScheduleCalculator(
            minimum_interval_seconds=configuration.jobs.minimum_interval_seconds
        ),
        lease_seconds=configuration.jobs.lease_seconds,
    )
    worker = None
    if mode == "worker":
        if authorize_execution is None:
            raise ValueError("worker runtime requires an execution authorizer")
        worker = JobWorker(
            repository,
            registry,
            worker_id=worker_id or f"worker-{uuid4()}",
            authorize_execution=authorize_execution,
            heartbeat_seconds=configuration.jobs.heartbeat_seconds,
        )
    return JobRuntime(mode=mode, registry=registry, repository=repository, worker=worker)
