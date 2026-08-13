from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import CroniterBadCronError, croniter

from .errors import JobValidationError
from .schemas import JobSchedule, ScheduleKind


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise JobValidationError("timestamp must include a timezone")
    return value.astimezone(UTC)


class ScheduleCalculator:
    def __init__(self, *, minimum_interval_seconds: int = 300) -> None:
        if minimum_interval_seconds < 300:
            raise ValueError("minimum interval cannot be lower than 300 seconds")
        self.minimum_interval_seconds = minimum_interval_seconds

    def validate(self, schedule: JobSchedule) -> None:
        if schedule.kind is ScheduleKind.MANUAL:
            if schedule.expression is not None or schedule.timezone is not None:
                raise JobValidationError("manual schedules cannot have expression or timezone")
            return
        if schedule.kind is ScheduleKind.INTERVAL:
            if schedule.timezone is not None:
                raise JobValidationError("interval schedules use UTC and cannot have a timezone")
            try:
                seconds = int(schedule.expression or "")
            except ValueError as error:
                raise JobValidationError("interval expression must be integer seconds") from error
            if str(seconds) != schedule.expression or seconds < self.minimum_interval_seconds:
                raise JobValidationError(
                    f"interval must be at least {self.minimum_interval_seconds} seconds"
                )
            return
        if schedule.kind is not ScheduleKind.CRON:
            raise JobValidationError("unsupported schedule kind")
        if not schedule.expression or len(schedule.expression.split()) != 5:
            raise JobValidationError("cron expression must have exactly five fields")
        if not schedule.timezone or len(schedule.timezone) > 64:
            raise JobValidationError("cron schedule requires an IANA timezone")
        try:
            ZoneInfo(schedule.timezone)
            croniter(schedule.expression, datetime(2026, 1, 1))
        except (ZoneInfoNotFoundError, CroniterBadCronError, ValueError) as error:
            raise JobValidationError("invalid cron expression or timezone") from error

    def next_after(self, schedule: JobSchedule, instant: datetime) -> datetime:
        self.validate(schedule)
        current = ensure_utc(instant)
        if schedule.kind is ScheduleKind.MANUAL:
            raise JobValidationError("manual schedules have no next occurrence")
        if schedule.kind is ScheduleKind.INTERVAL:
            return current + timedelta(seconds=int(schedule.expression or "0"))

        zone = ZoneInfo(schedule.timezone or "UTC")
        local_base = current.astimezone(zone).replace(tzinfo=None)
        iterator = croniter(schedule.expression, local_base)
        for _ in range(10_000):
            candidate = iterator.get_next(datetime).replace(tzinfo=None)
            instants: set[datetime] = set()
            for fold in (0, 1):
                aware = candidate.replace(tzinfo=zone, fold=fold)
                utc_candidate = aware.astimezone(UTC)
                if utc_candidate.astimezone(zone).replace(tzinfo=None) == candidate:
                    instants.add(utc_candidate)
            if len(instants) != 1:
                continue
            result = next(iter(instants))
            if result > current:
                return result
        raise JobValidationError("cron schedule has no bounded valid occurrence")
