from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any

from .database import connect
from .errors import JobConflict
from .handlers import JobTestOutcome, JobTypeRegistry
from .repository import JobRepository
from .schemas import JobSchedule


class JobApplicationService:
    def __init__(self, repository: JobRepository, registry: JobTypeRegistry) -> None:
        self.repository = repository
        self.registry = registry

    def list_types(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.registry.descriptors()]

    def list_jobs(self, workspace_id: str) -> list[dict[str, Any]]:
        return self.repository.list_jobs(workspace_id)

    def get_job(self, workspace_id: str, job_id: str) -> dict[str, Any]:
        return self.repository.get_job_detail(workspace_id, job_id)

    def create_job(
        self,
        *,
        workspace_id: str,
        actor_id: str,
        name: str,
        job_type: str,
        schedule: JobSchedule,
        type_config: object,
    ) -> dict[str, Any]:
        job = self.repository.create_job(
            workspace_id=workspace_id,
            actor_id=actor_id,
            name=name,
            job_type=job_type,
            schedule=schedule,
            type_config=type_config,
        )
        return self.get_job(workspace_id, job["id"])

    def update_job(
        self,
        *,
        workspace_id: str,
        job_id: str,
        actor_id: str,
        name: str,
        schedule: JobSchedule,
        type_config: object,
        expected_revision: int,
    ) -> dict[str, Any]:
        self.repository.update_definition(
            workspace_id=workspace_id,
            job_id=job_id,
            actor_id=actor_id,
            name=name,
            schedule=schedule,
            type_config=type_config,
            expected_revision=expected_revision,
        )
        return self.get_job(workspace_id, job_id)

    async def test_job(self, workspace_id: str, job_id: str, context: object) -> JobTestOutcome:
        with connect(self.repository.database_path) as connection:
            job = self.repository._required_job(connection, workspace_id, job_id)
            adapter = self.registry.get(job["job_type"])
            config = adapter.load_type_config(connection, job)
        result = await adapter.test(context, config)
        if not isinstance(result, JobTestOutcome):
            raise RuntimeError("Job Type returned an invalid test outcome")
        if result.success and not self.repository.mark_tested(
            workspace_id, job_id, job["definition_revision"]
        ):
            raise JobConflict("job definition changed while its test was running")
        return result
