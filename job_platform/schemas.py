from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class JobStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class ScheduleKind(StrEnum):
    MANUAL = "MANUAL"
    INTERVAL = "INTERVAL"
    CRON = "CRON"


class TriggerKind(StrEnum):
    MANUAL = "MANUAL"
    SCHEDULED = "SCHEDULED"
    RETRY = "RETRY"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    UNCHANGED = "UNCHANGED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_RUN_STATUSES = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.UNCHANGED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.CANCELLED}
)


@dataclass(frozen=True, slots=True)
class JobSchedule:
    kind: ScheduleKind
    expression: str | None = None
    timezone: str | None = None


@dataclass(frozen=True, slots=True)
class JobExecutionContext:
    run_id: str
    job_id: str
    workspace_id: str
    actor_id: str
    trigger_kind: TriggerKind
    scheduled_for: datetime
