from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .schemas import JobExecutionContext, RunStatus


@dataclass(frozen=True, slots=True)
class JobOutcome:
    status: RunStatus
    outcome_code: str

    def __post_init__(self) -> None:
        if self.status not in {RunStatus.SUCCEEDED, RunStatus.UNCHANGED, RunStatus.PARTIAL, RunStatus.FAILED}:
            raise ValueError("handler outcome must be a completion status")
        if not self.outcome_code or len(self.outcome_code) > 64:
            raise ValueError("outcome code must contain 1-64 characters")


@dataclass(frozen=True, slots=True)
class JobTestOutcome:
    success: bool
    code: str
    diagnostics: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.code or len(self.code) > 64:
            raise ValueError("test outcome code must contain 1-64 characters")


@dataclass(frozen=True, slots=True)
class JobTypeDescriptor:
    key: str
    label: str
    description: str
    icon: str
    editor_key: str


class JobTypeAdapter(Protocol):
    descriptor: JobTypeDescriptor

    def validate_config(self, payload: object) -> object: ...
    def create_type_row(self, connection: Any, job: dict[str, Any], config: object) -> None: ...
    def update_type_row(self, connection: Any, job: dict[str, Any], config: object) -> None: ...
    def load_type_config(self, connection: Any, job: dict[str, Any]) -> object: ...
    def project_job_detail(self, connection: Any, job: dict[str, Any]) -> dict[str, Any]: ...
    def project_run_detail(self, connection: Any, run: dict[str, Any]) -> dict[str, Any]: ...
    async def test(self, context: object, config: object) -> object: ...
    async def execute(self, context: JobExecutionContext) -> JobOutcome: ...
    async def retry(self, context: JobExecutionContext, source_run_id: str) -> JobOutcome: ...


class JobTypeRegistry:
    def __init__(self, adapters: tuple[JobTypeAdapter, ...] | list[JobTypeAdapter]) -> None:
        self._adapters: dict[str, JobTypeAdapter] = {}
        editors: set[str] = set()
        for adapter in adapters:
            descriptor = adapter.descriptor
            if not descriptor.key or descriptor.key in self._adapters:
                raise ValueError(f"duplicate Job Type key: {descriptor.key}")
            if not descriptor.editor_key or descriptor.editor_key in editors:
                raise ValueError(f"duplicate Job Type editor key: {descriptor.editor_key}")
            self._adapters[descriptor.key] = adapter
            editors.add(descriptor.editor_key)

    def get(self, key: str) -> JobTypeAdapter:
        try:
            return self._adapters[key]
        except KeyError as error:
            raise ValueError(f"unregistered Job Type: {key}") from error

    def descriptors(self) -> tuple[JobTypeDescriptor, ...]:
        return tuple(adapter.descriptor for adapter in self._adapters.values())

    def verify_persisted_types(self, connection: Any) -> None:
        persisted = {row[0] for row in connection.execute("SELECT DISTINCT job_type FROM jobs")}
        if unknown := persisted - self._adapters.keys():
            raise RuntimeError(f"Jobs database contains unregistered types: {', '.join(sorted(unknown))}")
