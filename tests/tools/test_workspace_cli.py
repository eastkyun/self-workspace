from __future__ import annotations

import re

from identity.passwords import CHEAPEST_TEST_PASSWORD_HASHER
from identity import bootstrap as bootstrap_module
from tests.migrations.legacy_workspace import initialize_legacy_workspace
from tools import workspace_cli


def test_cli_has_exact_bootstrap_interface_and_never_outputs_password(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        bootstrap_module,
        "WORKSPACE_ROOT",
        initialize_legacy_workspace(tmp_path / "legacy-workspace"),
    )
    password = "do-not-print-this-secret"
    monkeypatch.setenv("IDENTITY_DB_PATH", str(tmp_path / "identity.db"))
    monkeypatch.setenv("WORKSPACE_MIGRATION_MANIFEST_PATH", str(tmp_path / "manifest.json"))
    monkeypatch.setattr(workspace_cli.getpass, "getpass", lambda _prompt: password)
    monkeypatch.setattr(workspace_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        bootstrap_module,
        "_source_evidence",
        lambda: {
            "git_commit": "0" * 40,
            "git_branch": "test",
            "worktree_porcelain_sha256": "0" * 64,
            "application_version": "workspace-rbac-v1",
            "single_worker_asserted": True,
        },
    )

    original = workspace_cli.bootstrap_owner
    monkeypatch.setattr(
        workspace_cli,
        "bootstrap_owner",
        lambda email, supplied, **kwargs: original(
            email, supplied, hasher=CHEAPEST_TEST_PASSWORD_HASHER, **kwargs
        ),
    )
    assert workspace_cli.main(["bootstrap-owner", "--email", " User@Example.COM "]) == 0
    output = capsys.readouterr().out
    assert password not in output
    assert "user@example.com" in output
    assert re.search(r"workspace_id=[0-9a-f-]{36}", output)
    assert password not in (tmp_path / "manifest.json").read_text()


def test_cli_rejects_password_arguments_without_echoing_secret(monkeypatch, capsys):
    monkeypatch.setenv("PASSWORD", "environment-secret")
    secret = "never-echo-this-password"
    assert workspace_cli.main(
        ["bootstrap-owner", "--email", "user@example.com", "--password", secret]
    ) == 2
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "forbidden" in captured.err


def test_cli_rejects_equals_password_argument_without_echoing_secret(capsys):
    secret = "never-echo-this-equals-password"
    assert workspace_cli.main(
        ["bootstrap-owner", "--email", "user@example.com", f"--password={secret}"]
    ) == 2
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert "forbidden" in captured.err


def test_cli_rejects_invalid_email_and_noninteractive_password_without_mutation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IDENTITY_DB_PATH", str(tmp_path / "identity.db"))
    assert workspace_cli.main(["bootstrap-owner", "--email", "not-an-email"]) == 2
    assert not (tmp_path / "identity.db").exists()
    monkeypatch.setattr(workspace_cli.sys.stdin, "isatty", lambda: False)
    assert workspace_cli.main(["bootstrap-owner", "--email", "user@example.com"]) == 2
    assert not (tmp_path / "identity.db").exists()
    assert "interactive terminal" in capsys.readouterr().err


def test_cli_handles_password_prompt_eof_without_traceback_or_mutation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("IDENTITY_DB_PATH", str(tmp_path / "identity.db"))
    monkeypatch.setattr(workspace_cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(workspace_cli.getpass, "getpass", lambda _prompt: (_ for _ in ()).throw(EOFError()))
    assert workspace_cli.main(["bootstrap-owner", "--email", "user@example.com"]) == 2
    assert not (tmp_path / "identity.db").exists()
    assert "canceled" in capsys.readouterr().err
