from __future__ import annotations

import json

from tools.jobs import main


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
