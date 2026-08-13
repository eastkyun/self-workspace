from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from uuid import uuid4

from access.errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from access.service import AuthorizationService
from job_platform.database import connect, initialize_database, verify_database
from job_platform.handlers import JobTypeRegistry
from job_platform.health import health_snapshot
from job_platform.repository import JobRepository, timestamp
from job_platform.runtime import create_job_runtime
from shared.config import load_config
from web_collector.adapter import WebCollectionAdapter
from web_collector.database import initialize_database as initialize_web_collection_database
from web_collector.database import verify_web_collection_schema

VERSION = "1"


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="python -m tools.jobs")
    subcommands = command.add_subparsers(dest="command", required=True)
    subcommands.add_parser("initialize")
    subcommands.add_parser("verify")
    subcommands.add_parser("health")
    subcommands.add_parser("recover-stale")
    run_due = subcommands.add_parser("run-due")
    run_due.add_argument("--limit", type=int, default=50)
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
    invocation_id = str(uuid4())
    with connect(database) as connection:
        connection.execute(
            """INSERT INTO job_worker_invocations(
              id,worker_id,version,started_at,heartbeat_at,status
            ) VALUES (?,?,?,?,?,'RUNNING')""",
            (invocation_id, worker_id, VERSION, timestamp(now), timestamp(now)),
        )
    return invocation_id


def _record_finish(database, invocation_id: str, result: dict[str, int], *, failed: bool) -> None:
    with connect(database) as connection:
        connection.execute(
            """UPDATE job_worker_invocations SET heartbeat_at=?,finished_at=?,status=?,
              claimed_count=?,completed_count=?,failed_count=?,error_code=? WHERE id=?""",
            (
                timestamp(datetime.now(UTC)), timestamp(datetime.now(UTC)),
                "FAILED" if failed else "SUCCEEDED", result.get("claimed", 0),
                result.get("completed", 0), result.get("failed", 0),
                "WORKER_FAILED" if failed else None, invocation_id,
            ),
        )


async def run_due(limit: int) -> dict[str, int]:
    configuration = load_config()
    if not configuration.jobs.enabled:
        raise RuntimeError("Jobs execution is disabled")
    if not configuration.jobs.web_collection_enabled:
        raise RuntimeError("Web Collection execution is disabled")
    type_registry = registry(execution_enabled=True)
    authorization = AuthorizationService(configuration.databases.identity)
    worker_id = f"jobs-{uuid4()}"
    runtime = create_job_runtime(
        configuration,
        registry=type_registry,
        mode="worker",
        authorize_execution=_authorize(authorization),
        worker_id=worker_id,
    )
    invocation = _record_start(configuration.databases.jobs, worker_id, datetime.now(UTC))
    result: dict[str, int] = {}
    try:
        assert runtime.worker is not None
        result = await runtime.worker.run_due(limit=max(1, min(limit, 50)))
        _record_finish(configuration.databases.jobs, invocation, result, failed=False)
        return result
    except Exception:
        _record_finish(configuration.databases.jobs, invocation, result, failed=True)
        raise
    finally:
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
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
