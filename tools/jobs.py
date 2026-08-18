from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from datetime import UTC, datetime
from uuid import uuid4

from access.errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from access.service import AuthorizationService
from apps.notes.app.database import verify_database as verify_notes_database
from apps.notes.app.service import NotesService
from job_platform.database import connect, initialize_database, verify_database
from job_platform.handlers import JobTypeRegistry
from job_platform.health import health_snapshot
from job_platform.repository import JobRepository, timestamp
from job_platform.runtime import create_job_runtime
from job_platform.version import WORKER_PROTOCOL_VERSION
from shared.config import load_config
from shared.activity import SqliteActivitySink
from web_collector.adapter import WebCollectionAdapter
from web_collector.browser import BrowserManager
from web_collector.database import initialize_database as initialize_web_collection_database
from web_collector.database import verify_web_collection_schema
from web_collector.financial_briefing import OpenAIFinancialBriefingFormatter

VERSION = WORKER_PROTOCOL_VERSION
WORKER_COUNT_FIELDS = (
    "recovered",
    "enqueued",
    "claimed",
    "completed",
    "failed",
)


def _worker_event(event: str, counts: dict[str, int]) -> str:
    payload = {"event": event}
    for key in WORKER_COUNT_FIELDS:
        if key in counts:
            payload[key] = int(counts[key])
    return json.dumps(payload, separators=(",", ":"))


def _install_stop_signals(loop, stop: asyncio.Event) -> list[signal.Signals]:
    installed: list[signal.Signals] = []
    for signum in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signum, stop.set)
            installed.append(signum)
        except (NotImplementedError, RuntimeError):
            pass
    return installed


def _install_worker_version_guard(database, now: datetime) -> None:
    if not VERSION.isdigit():
        raise RuntimeError("Jobs worker version must be numeric")
    finished_at = timestamp(now)
    with connect(database) as connection:
        connection.execute("DROP TRIGGER IF EXISTS job_runs_current_worker_version")
        connection.execute(
            f"""CREATE TRIGGER job_runs_current_worker_version
                BEFORE UPDATE OF status ON job_runs
                WHEN NEW.status='RUNNING' AND COALESCE(
                    (SELECT version FROM job_worker_invocations
                     WHERE worker_id=NEW.worker_id
                     ORDER BY started_at DESC LIMIT 1), ''
                )!='{VERSION}'
                BEGIN
                    SELECT RAISE(ABORT, 'outdated jobs worker');
                END"""
        )
        connection.execute(
            """UPDATE job_worker_invocations
               SET status='STOPPED',finished_at=?,error_code='WORKER_SUPERSEDED'
               WHERE status='RUNNING' AND version!=?""",
            (finished_at, VERSION),
        )


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="python -m tools.jobs")
    subcommands = command.add_subparsers(dest="command", required=True)
    subcommands.add_parser("initialize")
    subcommands.add_parser("verify")
    subcommands.add_parser("health")
    subcommands.add_parser("recover-stale")
    run_due = subcommands.add_parser("run-due")
    run_due.add_argument("--limit", type=int, default=50)
    worker = subcommands.add_parser("worker")
    worker.add_argument("--limit", type=int, default=50)
    worker.add_argument("--poll-seconds", type=int, default=30)
    return command


def registry(*, execution_enabled: bool) -> JobTypeRegistry:
    return JobTypeRegistry([WebCollectionAdapter(execution_enabled=execution_enabled)])


def _authorize(authorization: AuthorizationService):
    def authorize(workspace_id: str, actor_id: str, job_type: str) -> bool:
        try:
            context = authorization.resolve_context(actor_id, workspace_id)
            snapshot = authorization.require(
                context,
                "jobs.update_own",
                actor_id,
                app_key="jobs",
            )
            if job_type == "WEB_COLLECTION":
                authorization.require(
                    context,
                    "notes.create",
                    app_key="notes",
                    snapshot=snapshot,
                )
            return True
        except (AppNotAvailable, PermissionDenied, WorkspaceNotAvailable):
            return False

    return authorize


def _record_start(database, worker_id: str, now: datetime) -> str:
    _install_worker_version_guard(database, now)
    invocation_id = str(uuid4())
    with connect(database) as connection:
        connection.execute(
            """INSERT INTO job_worker_invocations(
              id,worker_id,version,started_at,heartbeat_at,status
            ) VALUES (?,?,?,?,?,'RUNNING')""",
            (invocation_id, worker_id, VERSION, timestamp(now), timestamp(now)),
        )
    return invocation_id


def _record_heartbeat(
    database, invocation_id: str, result: dict[str, int] | None = None
) -> None:
    counts = result or {}
    with connect(database) as connection:
        connection.execute(
            """UPDATE job_worker_invocations SET heartbeat_at=?,claimed_count=?,
              completed_count=?,failed_count=? WHERE id=? AND status='RUNNING'""",
            (
                timestamp(datetime.now(UTC)), counts.get("claimed", 0),
                counts.get("completed", 0), counts.get("failed", 0), invocation_id,
            ),
        )


def _record_finish(
    database,
    invocation_id: str,
    result: dict[str, int],
    *,
    status: str,
) -> None:
    if status not in {"SUCCEEDED", "FAILED", "STOPPED"}:
        raise ValueError("invalid worker invocation status")
    with connect(database) as connection:
        connection.execute(
            """UPDATE job_worker_invocations SET heartbeat_at=?,finished_at=?,status=?,
              claimed_count=?,completed_count=?,failed_count=?,error_code=? WHERE id=?""",
            (
                timestamp(datetime.now(UTC)), timestamp(datetime.now(UTC)),
                status, result.get("claimed", 0),
                result.get("completed", 0), result.get("failed", 0),
                "WORKER_FAILED" if status == "FAILED" else None, invocation_id,
            ),
        )


def _worker_runtime(configuration):
    if not configuration.jobs.enabled:
        raise RuntimeError("Jobs execution is disabled")
    if not configuration.jobs.web_collection_enabled:
        raise RuntimeError("Web Collection execution is disabled")
    authorization = AuthorizationService(configuration.databases.identity)
    verify_notes_database(
        configuration.databases.notes,
        require_current=True,
        require_idempotency=True,
    )
    notes_service = NotesService(
        configuration.databases.notes,
        authorization=authorization,
        activity_sink=SqliteActivitySink(configuration.databases.identity),
    )
    openai_api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    financial_formatter = (
        OpenAIFinancialBriefingFormatter(
            api_key=openai_api_key,
            model=configuration.jobs.openai_model,
            timeout_seconds=configuration.jobs.openai_timeout_seconds,
        )
        if openai_api_key
        else None
    )
    type_registry = JobTypeRegistry(
        [
            WebCollectionAdapter(
                execution_enabled=True,
                browser_manager=BrowserManager(
                    concurrency=configuration.jobs.browser_concurrency
                ),
                database_path=configuration.databases.jobs,
                notes_service=notes_service,
                context_resolver=lambda workspace_id, actor_id: (
                    authorization.resolve_context(actor_id, workspace_id)
                ),
                financial_briefing_formatter=financial_formatter,
            )
        ]
    )
    worker_id = f"jobs-{uuid4()}"
    return create_job_runtime(
        configuration,
        registry=type_registry,
        mode="worker",
        authorize_execution=_authorize(authorization),
        worker_id=worker_id,
    )


async def _run_cycle(runtime, invocation: str, totals: dict[str, int], *, limit: int):
    assert runtime.worker is not None
    operation = asyncio.create_task(runtime.worker.run_due(limit=limit))
    try:
        while True:
            done, _ = await asyncio.wait(
                {operation}, timeout=runtime.worker.heartbeat_seconds
            )
            if operation in done:
                result = operation.result()
                for key, value in result.items():
                    totals[key] = totals.get(key, 0) + value
                _record_heartbeat(runtime.repository.database_path, invocation, totals)
                return result
            _record_heartbeat(runtime.repository.database_path, invocation, totals)
    except BaseException:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)
        raise


async def run_due(limit: int) -> dict[str, int]:
    configuration = load_config()
    runtime = _worker_runtime(configuration)
    worker_id = runtime.worker.worker_id
    invocation = _record_start(configuration.databases.jobs, worker_id, datetime.now(UTC))
    totals: dict[str, int] = {}
    try:
        result = await _run_cycle(
            runtime, invocation, totals, limit=max(1, min(limit, 50))
        )
        _record_finish(
            configuration.databases.jobs, invocation, result, status="SUCCEEDED"
        )
        return result
    except Exception:
        _record_finish(configuration.databases.jobs, invocation, totals, status="FAILED")
        raise
    finally:
        await runtime.close()


async def run_worker(
    *,
    limit: int,
    poll_seconds: int,
    stop_event: asyncio.Event | None = None,
) -> dict[str, int]:
    if poll_seconds < 5 or poll_seconds > 3600:
        raise RuntimeError("worker poll interval must be between 5 and 3600 seconds")
    configuration = load_config()
    runtime = _worker_runtime(configuration)
    assert runtime.worker is not None
    invocation = _record_start(
        configuration.databases.jobs, runtime.worker.worker_id, datetime.now(UTC)
    )
    totals: dict[str, int] = {}
    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    installed_signals = _install_stop_signals(loop, stop)
    final_status = "STOPPED"
    try:
        while not stop.is_set():
            cycle = await _run_cycle(
                runtime,
                invocation,
                totals,
                limit=max(1, min(limit, 50)),
            )
            print(_worker_event("jobs.worker.cycle", cycle), flush=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
            except TimeoutError:
                pass
        return totals
    except Exception:
        final_status = "FAILED"
        raise
    finally:
        for signum in installed_signals:
            loop.remove_signal_handler(signum)
        _record_finish(
            configuration.databases.jobs,
            invocation,
            totals,
            status=final_status,
        )
        await runtime.close()


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    configuration = load_config()
    database = configuration.databases.jobs
    if args.command == "initialize":
        initialize_database(database)
        initialize_web_collection_database(database)
        print(json.dumps({"status": "initialized", "schema_version": 1}))
        return 0
    verify_database(database)
    verify_web_collection_schema(database)
    if args.command == "verify":
        print(json.dumps({"status": "verified", "schema_version": 1}))
        return 0
    if args.command == "health":
        print(json.dumps(health_snapshot(database), ensure_ascii=False))
        return 0
    if args.command == "recover-stale":
        repository = JobRepository(database, registry(execution_enabled=False))
        recovered = repository.recover_stale()
        print(json.dumps({"recovered": len(recovered)}))
        return 0
    if args.command == "run-due":
        print(json.dumps(asyncio.run(run_due(args.limit))))
        return 0
    if args.command == "worker":
        result = asyncio.run(
            run_worker(limit=args.limit, poll_seconds=args.poll_seconds)
        )
        print(_worker_event("jobs.worker.stopped", result))
        return 0
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
