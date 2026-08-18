from __future__ import annotations

import asyncio
import json
import signal
from datetime import UTC, datetime
from types import SimpleNamespace

from job_platform.database import connect
from job_platform.version import WORKER_PROTOCOL_VERSION
import tools.jobs as jobs_tool
from tools.jobs import _install_stop_signals, _record_start, _worker_event, main


def test_jobs_cli_initializes_verifies_and_reports_bounded_health(tmp_path, monkeypatch, capsys):
    database = tmp_path / "jobs.db"
    monkeypatch.setenv("JOBS_DB_PATH", str(database))
    assert main(["initialize"]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized == {"status": "initialized", "schema_version": 1}

    assert main(["verify"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "verified"

    assert main(["health"]) == 0
    health = json.loads(capsys.readouterr().out)
    assert health["queue"] == {"depth": 0, "oldest_scheduled_for": None}
    assert "database" not in health
    assert "path" not in json.dumps(health)


def test_run_due_refuses_disabled_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.db"))
    main(["initialize"])
    try:
        main(["run-due"])
    except RuntimeError as error:
        assert str(error) == "Jobs execution is disabled"
    else:
        raise AssertionError("run-due must fail closed")


def test_worker_refuses_disabled_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.db"))
    main(["initialize"])
    try:
        main(["worker", "--poll-seconds", "30"])
    except RuntimeError as error:
        assert str(error) == "Jobs execution is disabled"
    else:
        raise AssertionError("worker must fail closed")


def test_worker_refuses_disabled_web_collection(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBS_DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setenv("JOBS_ENABLED", "true")
    monkeypatch.setenv("JOBS_WEB_COLLECTION_ENABLED", "false")
    main(["initialize"])
    try:
        main(["worker", "--poll-seconds", "30"])
    except RuntimeError as error:
        assert str(error) == "Web Collection execution is disabled"
    else:
        raise AssertionError("Web Collection kill switch must fail closed")


def test_current_worker_start_supersedes_old_versions_and_installs_guard(
    tmp_path, monkeypatch
):
    database = tmp_path / "jobs.db"
    monkeypatch.setenv("JOBS_DB_PATH", str(database))
    main(["initialize"])
    now = datetime(2026, 8, 14, tzinfo=UTC)
    with connect(database) as connection:
        connection.execute(
            """INSERT INTO job_worker_invocations(
                 id,worker_id,version,started_at,heartbeat_at,status
               ) VALUES ('old','old-worker','1',?,?, 'RUNNING')""",
            (now.isoformat(), now.isoformat()),
        )

    _record_start(database, "current-worker", now)

    with connect(database) as connection:
        old = connection.execute(
            "SELECT status,error_code FROM job_worker_invocations WHERE id='old'"
        ).fetchone()
        trigger = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='job_runs_current_worker_version'"
        ).fetchone()
    assert dict(old) == {
        "status": "STOPPED",
        "error_code": "WORKER_SUPERSEDED",
    }
    assert f"!='{WORKER_PROTOCOL_VERSION}'" in trigger["sql"]


def test_signal_handlers_request_graceful_stop():
    callbacks = {}
    removed = []

    class FakeLoop:
        def add_signal_handler(self, signum, callback):
            callbacks[signum] = callback

        def remove_signal_handler(self, signum):
            removed.append(signum)

    event = asyncio.Event()
    loop = FakeLoop()
    installed = _install_stop_signals(loop, event)

    assert installed == [signal.SIGINT, signal.SIGTERM]
    callbacks[signal.SIGTERM]()
    assert event.is_set()
    for signum in installed:
        loop.remove_signal_handler(signum)
    assert removed == installed


def test_worker_stop_closes_runtime_and_records_bounded_structured_log(
    tmp_path, monkeypatch, capsys
):
    closed = []
    finishes = []
    stop = asyncio.Event()

    class FakeRuntime:
        worker = SimpleNamespace(worker_id="worker-test", heartbeat_seconds=1)
        repository = SimpleNamespace(database_path=tmp_path / "jobs.db")

        async def close(self):
            closed.append(True)

    async def fake_cycle(_runtime, _invocation, _totals, *, limit):
        assert limit == 7
        stop.set()
        result = {
            "recovered": 0,
            "enqueued": 1,
            "claimed": 1,
            "completed": 1,
            "failed": 0,
            "url": "https://secret.example/path",
        }
        _totals.update(result)
        return result

    monkeypatch.setattr(
        jobs_tool,
        "load_config",
        lambda: SimpleNamespace(
            databases=SimpleNamespace(jobs=tmp_path / "jobs.db")
        ),
    )
    monkeypatch.setattr(jobs_tool, "_worker_runtime", lambda _config: FakeRuntime())
    monkeypatch.setattr(jobs_tool, "_record_start", lambda *_args: "invocation")
    monkeypatch.setattr(jobs_tool, "_run_cycle", fake_cycle)
    monkeypatch.setattr(
        jobs_tool,
        "_record_finish",
        lambda _database, _invocation, result, *, status: finishes.append(
            (status, dict(result))
        ),
    )

    result = asyncio.run(
        jobs_tool.run_worker(limit=7, poll_seconds=30, stop_event=stop)
    )

    output = capsys.readouterr().out.strip()
    assert result["completed"] == 1
    assert json.loads(output) == {
        "event": "jobs.worker.cycle",
        "recovered": 0,
        "enqueued": 1,
        "claimed": 1,
        "completed": 1,
        "failed": 0,
    }
    assert "secret.example" not in output
    assert finishes == [("STOPPED", result)]
    assert closed == [True]


def test_worker_event_drops_unbounded_or_sensitive_fields():
    payload = _worker_event(
        "jobs.worker.cycle",
        {
            "claimed": 2,
            "failed": 1,
            "database": "/secret/jobs.db",
            "raw_error": "token=secret",
        },
    )
    assert json.loads(payload) == {
        "event": "jobs.worker.cycle",
        "claimed": 2,
        "failed": 1,
    }
    assert "secret" not in payload
