from __future__ import annotations

import sqlite3
import shutil
import subprocess

import pytest

from identity import bootstrap as bootstrap_module
from identity.bootstrap import BootstrapError, bootstrap_owner
from identity.database import initialize_database
from identity.foundation import (
    BUILTIN_ROLES,
    FOUNDATION_APPS,
    OWNER_ONLY_PERMISSION_CODES,
    foundation_catalog_checksum,
    role_permission_codes,
)
from identity.passwords import CHEAPEST_TEST_PASSWORD_HASHER
from shared.migrations import read_manifest
from tests.migrations.legacy_workspace import initialize_legacy_workspace


PASSWORD = "bootstrap-test-password"
ORIGINAL_SOURCE_EVIDENCE = bootstrap_module._source_evidence


@pytest.fixture(autouse=True)
def _fixed_clean_source_evidence(monkeypatch, tmp_path):
    monkeypatch.setattr(
        bootstrap_module,
        "WORKSPACE_ROOT",
        initialize_legacy_workspace(tmp_path / "legacy-workspace"),
    )
    monkeypatch.setenv("WORKSPACE_SINGLE_WORKER_ASSERTED", "true")
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


def _bootstrap(tmp_path, **kwargs):
    return bootstrap_owner(
        "  ＯＷＮＥＲ@Example.COM ",
        PASSWORD,
        database_path=tmp_path / "identity.db",
        manifest_path=tmp_path / "migration-manifest.json",
        hasher=CHEAPEST_TEST_PASSWORD_HASHER,
        **kwargs,
    )


def test_bootstrap_creates_complete_personal_owner_foundation_and_manifest(tmp_path):
    result = _bootstrap(tmp_path)
    assert result.created is True
    with sqlite3.connect(tmp_path / "identity.db") as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute("SELECT email_normalized FROM users").fetchone()[0] == "owner@example.com"
        workspace = connection.execute("SELECT * FROM workspaces").fetchone()
        assert workspace["id"] == result.workspace_id
        assert workspace["type"] == "PERSONAL"
        assert workspace["status"] == "ACTIVE"
        roles = connection.execute("SELECT key, system_role FROM roles ORDER BY key").fetchall()
        assert {(row["key"], row["system_role"]) for row in roles} == {(key, system) for key, _, system in BUILTIN_ROLES}
        apps = connection.execute("SELECT key, enabled FROM apps JOIN workspace_apps ON apps.id=workspace_apps.app_id ORDER BY display_order").fetchall()
        assert [(row["key"], row["enabled"]) for row in apps] == [(app.key, int(app.default_enabled)) for app in FOUNDATION_APPS]
        assert {row["key"] for row in apps if row["enabled"]} == {
            "home", "notes", "calendar", "search", "publishing", "assets"
        }
        owner_only = {row["code"] for row in connection.execute("SELECT code FROM permissions WHERE owner_only=1")}
        assert owner_only == OWNER_ONLY_PERMISSION_CODES
        permission_apps = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT apps.key FROM permissions "
                "JOIN apps ON apps.id=permissions.app_id"
            )
        }
        assert permission_apps == {"notes", "calendar", "search", "publishing", "assets", "workspace"}
        for role_key, _, _ in BUILTIN_ROLES:
            actual = {row[0] for row in connection.execute("SELECT permission.code FROM role_permissions binding JOIN roles role ON role.id=binding.role_id JOIN permissions permission ON permission.id=binding.permission_id WHERE role.key=?", (role_key,))}
            assert actual == role_permission_codes(role_key)
        admin_permissions = role_permission_codes("admin")
        assert admin_permissions.isdisjoint(OWNER_ONLY_PERMISSION_CODES)
        audit = connection.execute("SELECT action, actor_user_id, target_id FROM audit_logs").fetchone()
        assert tuple(audit) == ("bootstrap.owner.created", result.user_id, result.workspace_id)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    manifest = read_manifest(tmp_path / "migration-manifest.json")
    assert manifest["release_id"] == result.release_id
    assert manifest["bootstrap"]["user_id"] == result.user_id
    assert manifest["bootstrap"]["workspace_id"] == result.workspace_id
    assert manifest["artifacts"]["identity"]["state"] == "verified"
    assert manifest["artifacts"]["calendar"]["state"] == "pending"
    assert manifest["catalog"]["sha256_before"] != manifest["catalog"]["manifest_checksum_after"]
    assert manifest["catalog"]["manifest_checksum_after"] == foundation_catalog_checksum()
    assert "owner@example.com" not in (tmp_path / "migration-manifest.json").read_text()


def test_exact_retry_is_idempotent_and_recovers_missing_manifest(tmp_path):
    first = _bootstrap(tmp_path)
    (tmp_path / "migration-manifest.json").unlink()
    second = _bootstrap(tmp_path)
    assert second.created is False
    assert (second.user_id, second.workspace_id, second.membership_id) == (first.user_id, first.workspace_id, first.membership_id)
    assert read_manifest(tmp_path / "migration-manifest.json")["bootstrap"]["user_id"] == first.user_id
    with sqlite3.connect(tmp_path / "identity.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 1


def test_mismatched_retry_and_partial_state_fail_without_mutation(tmp_path):
    _bootstrap(tmp_path)
    with pytest.raises(BootstrapError, match="exactly match"):
        bootstrap_owner("owner@example.com", "different", database_path=tmp_path / "identity.db", manifest_path=tmp_path / "migration-manifest.json", hasher=CHEAPEST_TEST_PASSWORD_HASHER)
    with sqlite3.connect(tmp_path / "identity.db") as connection:
        before = connection.total_changes
        connection.execute("INSERT INTO audit_logs SELECT workspace_id, actor_user_id, 'extra', target_type, target_id, metadata_json, created_at FROM audit_logs")
        connection.commit()
        assert connection.total_changes == before + 1
    with pytest.raises(BootstrapError, match="partial or non-bootstrap"):
        _bootstrap(tmp_path)


@pytest.mark.parametrize("stage", ["user", "workspace", "roles", "catalog", "membership", "bindings", "audit"])
def test_failure_inside_identity_transaction_rolls_back_all_seed_rows(tmp_path, stage):
    def failpoint(current):
        if current == stage:
            raise RuntimeError("injected failure")

    with pytest.raises(RuntimeError, match="injected"):
        _bootstrap(tmp_path, _failpoint=failpoint)
    with sqlite3.connect(tmp_path / "identity.db") as connection:
        for table in ("users", "workspaces", "apps", "roles", "workspace_members", "permissions", "role_permissions", "audit_logs"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert not (tmp_path / "migration-manifest.json").exists()


def test_failure_after_db_commit_is_recoverable_by_exact_retry(tmp_path):
    def failpoint(stage):
        if stage == "identity_committed":
            raise RuntimeError("power loss before manifest")

    with pytest.raises(RuntimeError, match="power loss"):
        _bootstrap(tmp_path, _failpoint=failpoint)
    assert not (tmp_path / "migration-manifest.json").exists()
    recovered = _bootstrap(tmp_path)
    assert recovered.created is False
    assert (tmp_path / "migration-manifest.json").exists()


def test_manifest_durability_failure_preserves_committed_identity_for_retry(tmp_path, monkeypatch):
    real_write = bootstrap_module.write_manifest
    calls = 0

    def fail_first_write(path, manifest):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected fsync failure")
        return real_write(path, manifest)

    monkeypatch.setattr(bootstrap_module, "write_manifest", fail_first_write)
    with pytest.raises(OSError, match="fsync failure"):
        _bootstrap(tmp_path)
    with sqlite3.connect(tmp_path / "identity.db") as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 1

    recovered = _bootstrap(tmp_path)
    assert recovered.created is False
    assert calls == 2
    assert read_manifest(tmp_path / "migration-manifest.json")["bootstrap"]["user_id"] == recovered.user_id


def test_preexisting_manifest_is_validated_before_empty_database_mutation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _bootstrap(source)
    target_db = initialize_database(tmp_path / "empty.db")
    target_manifest = tmp_path / "existing-manifest.json"
    shutil.copyfile(source / "migration-manifest.json", target_manifest)

    with pytest.raises(BootstrapError, match="identity database is empty"):
        bootstrap_owner(
            "owner@example.com", PASSWORD, database_path=target_db,
            manifest_path=target_manifest, hasher=CHEAPEST_TEST_PASSWORD_HASHER,
        )
    with sqlite3.connect(target_db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 0


def test_mismatched_manifest_email_fails_before_database_creation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _bootstrap(source)
    target_db = tmp_path / "must-not-be-created.db"
    with pytest.raises(BootstrapError, match="different bootstrap identity"):
        bootstrap_owner(
            "other@example.com", PASSWORD, database_path=target_db,
            manifest_path=source / "migration-manifest.json",
            hasher=CHEAPEST_TEST_PASSWORD_HASHER,
        )
    assert not target_db.exists()


def test_matching_manifest_with_missing_database_fails_before_creation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    _bootstrap(source)
    missing = tmp_path / "missing.db"
    with pytest.raises(BootstrapError, match="identity database is missing"):
        bootstrap_owner(
            "owner@example.com", PASSWORD, database_path=missing,
            manifest_path=source / "migration-manifest.json",
            hasher=CHEAPEST_TEST_PASSWORD_HASHER,
        )
    assert not missing.exists()


def test_git_evidence_failure_is_explicit_and_precedes_database_mutation(tmp_path, monkeypatch):
    def fail_git(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, "git")

    monkeypatch.setattr(bootstrap_module, "_source_evidence", ORIGINAL_SOURCE_EVIDENCE)
    monkeypatch.setattr(bootstrap_module.subprocess, "run", fail_git)
    with pytest.raises(BootstrapError, match="Git worktree evidence"):
        _bootstrap(tmp_path)
    assert not (tmp_path / "identity.db").exists()


def test_single_worker_assertion_is_required_before_database_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap_module, "_source_evidence", ORIGINAL_SOURCE_EVIDENCE)
    monkeypatch.delenv("WORKSPACE_SINGLE_WORKER_ASSERTED", raising=False)
    with pytest.raises(BootstrapError, match="SINGLE_WORKER_ASSERTED=true"):
        _bootstrap(tmp_path)
    assert not (tmp_path / "identity.db").exists()


def test_dirty_worktree_is_rejected_before_database_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr(bootstrap_module, "_source_evidence", ORIGINAL_SOURCE_EVIDENCE)

    def dirty_status(args, **_kwargs):
        assert tuple(args) == ("git", "status", "--porcelain=v1")
        return subprocess.CompletedProcess(args, 0, stdout=" M identity/bootstrap.py\n", stderr="")

    monkeypatch.setattr(bootstrap_module.subprocess, "run", dirty_status)
    with pytest.raises(BootstrapError, match="clean Git worktree"):
        _bootstrap(tmp_path)
    assert not (tmp_path / "identity.db").exists()
