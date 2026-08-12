from __future__ import annotations

import sqlite3
from copy import deepcopy
from uuid import uuid4

import pytest

from shared.migrations import (
    ManifestError,
    MigrationError,
    MigrationStep,
    advance_artifact,
    get_user_version,
    migrate,
    read_manifest,
    write_manifest,
)
from shared.migrations.verification import DatabaseBoundaryError, DatabaseVerificationError


def _create_items(connection: sqlite3.Connection, _context: object) -> None:
    connection.execute(
        "CREATE TABLE items (id INTEGER PRIMARY KEY, parent_id INTEGER "
        "REFERENCES items(id), name TEXT NOT NULL)"
    )


def _verify_items(connection: sqlite3.Connection, _context: object) -> None:
    columns = connection.execute("PRAGMA table_info(items)").fetchall()
    assert [row[1] for row in columns] == ["id", "parent_id", "name"]
    assert get_user_version(connection) == 1


def test_fresh_init_sets_explicit_version_and_verifies_database(tmp_path):
    with sqlite3.connect(tmp_path / "fresh.db") as connection:
        result = migrate(
            connection,
            target_version=1,
            migrations=[MigrationStep(0, 1, _create_items, _verify_items)],
        )

        assert result.from_version == 0
        assert result.to_version == 1
        assert result.applied_versions == (1,)
        assert result.changed is True
        assert get_user_version(connection) == 1
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migrate_multiple_versions_and_repeat_is_idempotent(tmp_path):
    apply_calls: list[int] = []
    verify_calls: list[int] = []

    def add_description(connection, _context):
        apply_calls.append(2)
        connection.execute("ALTER TABLE items ADD COLUMN description TEXT")

    def verify_description(connection, _context):
        verify_calls.append(2)
        names = [row[1] for row in connection.execute("PRAGMA table_info(items)")]
        assert names[-1] == "description"
        assert get_user_version(connection) == 2

    steps = [
        MigrationStep(0, 1, _create_items, _verify_items),
        MigrationStep(1, 2, add_description, verify_description),
    ]
    with sqlite3.connect(tmp_path / "migrate.db") as connection:
        first = migrate(connection, target_version=2, migrations=steps)
        second = migrate(connection, target_version=2, migrations=steps)

        assert first.applied_versions == (1, 2)
        assert second.applied_versions == ()
        assert second.changed is False
        assert apply_calls == [2]
        assert verify_calls == [2, 2]
        assert get_user_version(connection) == 2


def test_repeat_verification_cannot_mutate_or_attach(tmp_path):
    attempts = 0

    def verify(connection, _context):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            connection.execute("INSERT INTO items(name) VALUES ('not verification')")
        _verify_items(connection, _context)

    step = MigrationStep(0, 1, _create_items, verify)
    with sqlite3.connect(tmp_path / "readonly-verify.db") as connection:
        migrate(connection, target_version=1, migrations=[step])
        with pytest.raises(MigrationError, match="verification for database version 1"):
            migrate(connection, target_version=1, migrations=[step])
        assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        assert connection.in_transaction is False

def test_interrupted_migration_rolls_back_schema_data_and_version(tmp_path):
    def interrupted(connection, _context):
        connection.execute("CREATE TABLE partial (value TEXT NOT NULL)")
        connection.execute("INSERT INTO partial VALUES ('must roll back')")
        raise RuntimeError("simulated interruption")

    with sqlite3.connect(tmp_path / "interrupted.db") as connection:
        with pytest.raises(MigrationError, match="v0 to v1 failed"):
            migrate(
                connection,
                target_version=1,
                migrations=[MigrationStep(0, 1, interrupted, lambda *_: None)],
            )

        assert get_user_version(connection) == 0
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'partial'"
        ).fetchone() is None
        assert connection.in_transaction is False


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_process_interruption_rolls_back_and_can_resume(tmp_path, interruption):
    calls = 0

    def interrupt_once(connection, _context):
        nonlocal calls
        connection.execute("ALTER TABLE items ADD COLUMN description TEXT")
        calls += 1
        if calls == 1:
            raise interruption()

    steps = [
        MigrationStep(0, 1, _create_items, _verify_items),
        MigrationStep(1, 2, interrupt_once, lambda *_: None),
    ]
    with sqlite3.connect(tmp_path / "resume.db") as connection:
        with pytest.raises(interruption):
            migrate(connection, target_version=2, migrations=steps)
        assert get_user_version(connection) == 1
        assert "description" not in [
            row[1] for row in connection.execute("PRAGMA table_info(items)")
        ]
        result = migrate(connection, target_version=2, migrations=steps)
        assert result.applied_versions == (2,)
        assert get_user_version(connection) == 2


@pytest.mark.parametrize(
    "boundary_sql", ["ATTACH DATABASE ':memory:' AS other", "COMMIT"]
)
def test_migration_callback_cannot_escape_database_or_transaction_boundary(
    tmp_path, boundary_sql
):
    def escape(connection, _context):
        connection.execute("CREATE TABLE partial (value TEXT)")
        connection.execute(boundary_sql)

    with sqlite3.connect(tmp_path / "boundary.db") as connection:
        with pytest.raises(MigrationError, match="v0 to v1 failed"):
            migrate(
                connection,
                target_version=1,
                migrations=[MigrationStep(0, 1, escape, lambda *_: None)],
            )

        assert get_user_version(connection) == 0
        databases = connection.execute("PRAGMA database_list").fetchall()
        assert [(row[1], row[2]) for row in databases if row[1] != "temp"] == [
            ("main", str(tmp_path / "boundary.db"))
        ]


def test_runner_rejects_pre_attached_database(tmp_path):
    with sqlite3.connect(tmp_path / "main.db") as connection:
        connection.execute(f"ATTACH DATABASE '{tmp_path / 'other.db'}' AS other")
        with pytest.raises(DatabaseBoundaryError, match="other"):
            migrate(connection, target_version=0, migrations=[])


def test_verification_failure_rolls_back_migration(tmp_path):
    def fail_verify(_connection, _context):
        raise AssertionError("schema does not match")

    with sqlite3.connect(tmp_path / "verify.db") as connection:
        with pytest.raises(MigrationError, match="v0 to v1 failed"):
            migrate(
                connection,
                target_version=1,
                migrations=[MigrationStep(0, 1, _create_items, fail_verify)],
            )
        assert get_user_version(connection) == 0
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'items'"
        ).fetchone() is None


def test_missing_migration_path_is_rejected_before_any_change(tmp_path):
    with sqlite3.connect(tmp_path / "missing.db") as connection:
        with pytest.raises(MigrationError, match="no migration from version 1"):
            migrate(
                connection,
                target_version=2,
                migrations=[MigrationStep(0, 1, _create_items, _verify_items)],
            )

        assert get_user_version(connection) == 0
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'items'"
        ).fetchone() is None


def test_rejects_duplicate_invalid_and_newer_versions(tmp_path):
    noop = lambda *_: None
    with sqlite3.connect(tmp_path / "versions.db") as connection:
        with pytest.raises(MigrationError, match="duplicate"):
            migrate(
                connection,
                target_version=1,
                migrations=[MigrationStep(0, 1, noop, noop), MigrationStep(0, 1, noop, noop)],
            )
        with pytest.raises(MigrationError, match="adjacent"):
            migrate(
                connection,
                target_version=2,
                migrations=[MigrationStep(0, 2, noop, noop)],
            )
        connection.execute("PRAGMA user_version = 3")
        with pytest.raises(MigrationError, match="newer than target"):
            migrate(connection, target_version=2, migrations=[])


def test_context_is_propagated_to_apply_and_verify(tmp_path):
    seen = []
    context = {"workspace_id": "workspace-1"}

    def record_apply(connection, values):
        seen.append(("apply", values))
        _create_items(connection, values)

    def record_verify(connection, values):
        seen.append(("verify", values))
        _verify_items(connection, values)

    with sqlite3.connect(tmp_path / "context.db") as connection:
        migrate(
            connection,
            target_version=1,
            migrations=[MigrationStep(0, 1, record_apply, record_verify)],
            context=context,
        )
    assert seen == [("apply", context), ("verify", context)]


def test_foreign_key_verification_rejects_orphaned_source(tmp_path):
    with sqlite3.connect(tmp_path / "orphans.db") as connection:
        connection.executescript(
            "CREATE TABLE parent (id INTEGER PRIMARY KEY);"
            "CREATE TABLE child (parent_id INTEGER REFERENCES parent(id));"
            "INSERT INTO child VALUES (99);"
        )
        connection.commit()

        with pytest.raises(DatabaseVerificationError, match="foreign_key_check"):
            migrate(connection, target_version=0, migrations=[])


def test_repeat_requires_a_current_version_verifier(tmp_path):
    with sqlite3.connect(tmp_path / "unverified.db") as connection:
        connection.execute("PRAGMA user_version = 1")
        with pytest.raises(MigrationError, match="no verifier"):
            migrate(connection, target_version=1, migrations=[])


@pytest.mark.parametrize("pragma", ["user_version = 99", "application_id = 99"])
def test_verify_cannot_persist_write_pragma_after_disabling_authorizer(tmp_path, pragma):
    def malicious_verify(connection, _context):
        connection.set_authorizer(None)
        connection.execute(f"PRAGMA {pragma}")

    with sqlite3.connect(tmp_path / "pragma.db") as connection:
        with pytest.raises(MigrationError):
            migrate(connection, target_version=1, migrations=[MigrationStep(0, 1, _create_items, malicious_verify)])
        assert get_user_version(connection) == 0
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 0


def test_callback_cannot_disable_authorizer_and_leave_attached_database(tmp_path):
    def malicious_apply(connection, _context):
        connection.set_authorizer(None)
        connection.execute("ATTACH DATABASE ':memory:' AS escaped")

    with sqlite3.connect(tmp_path / "escaped.db") as connection:
        with pytest.raises(MigrationError):
            migrate(connection, target_version=1, migrations=[MigrationStep(0, 1, malicious_apply, lambda *_: None)])
        assert get_user_version(connection) == 0
        assert "escaped" not in [row[1] for row in connection.execute("PRAGMA database_list")]


def test_callback_receives_restricted_facade_and_cannot_commit_external_write(tmp_path):
    external = tmp_path / "external.db"
    with sqlite3.connect(external) as other:
        other.execute("CREATE TABLE records(value TEXT)")

    def escape(connection, _context):
        assert not hasattr(connection, "commit")
        assert not hasattr(connection, "rollback")
        assert not hasattr(connection, "executescript")
        connection.set_authorizer(None)
        connection.execute(f"ATTACH DATABASE '{external}' AS other")
        connection.execute("INSERT INTO other.records VALUES ('escaped')")
        connection.execute("COMMIT")

    with sqlite3.connect(tmp_path / "facade.db") as connection:
        with pytest.raises(MigrationError):
            migrate(connection, target_version=1, migrations=[MigrationStep(0, 1, escape, lambda *_: None)])
    with sqlite3.connect(external) as other:
        assert other.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0


def test_first_run_verifier_cannot_remove_authorizer_and_insert(tmp_path):
    def malicious_verify(connection, _context):
        connection.set_authorizer(None)
        connection.execute("INSERT INTO items(name) VALUES ('escaped')")

    with sqlite3.connect(tmp_path / "first-verify.db") as connection:
        with pytest.raises(MigrationError):
            migrate(connection, target_version=1, migrations=[MigrationStep(0, 1, _create_items, malicious_verify)])
        assert get_user_version(connection) == 0
        assert connection.execute("SELECT name FROM sqlite_master WHERE name='items'").fetchone() is None


def _manifest():
    release_id = str(uuid4())
    zero_hash = "0" * 64
    db_evidence = {"bytes": 0, "file_sha256": zero_hash, "schema_sha256": zero_hash, "content_sha256": zero_hash, "user_version": 1, "rows": {}, "quick_check": "ok", "foreign_key_orphans": 0}
    file_evidence = {"bytes": 0, "sha256": zero_hash}
    blob_evidence = {"files": [], "manifest_sha256": zero_hash}
    artifacts = {}
    for index, name in enumerate(("identity", "calendar", "notes", "publishing", "assets", "publication_types", "asset_blobs")):
        evidence = db_evidence if index < 5 else file_evidence if name == "publication_types" else blob_evidence
        transitions = {"identity": (0, 4), "calendar": (2, 3), "notes": (1, 2), "publishing": (1, 2), "assets": (1, 2), "publication_types": (1, 1), "asset_blobs": (1, 1)}
        source_path = None if name == "identity" else f"source/{name}"
        before = None if source_path is None else deepcopy(evidence)
        after = deepcopy(evidence)
        if index < 5:
            if before is not None: before["user_version"] = transitions[name][0]
            after["user_version"] = transitions[name][1]
        artifacts[name] = {"source_path": source_path, "staging_path": f"staging/{name}", "target_path": f"target/{name}", "from_version": transitions[name][0], "to_version": transitions[name][1], "release_id": release_id, "state": "pending", "before": before, "after": after}
    return {"manifest_version": 1, "release_id": release_id, "created_at": "2026-08-03T00:00:00Z", "source": {"git_commit": "0" * 40, "git_branch": "feature/test", "worktree_porcelain_sha256": zero_hash, "application_version": "test", "single_worker_asserted": True}, "bootstrap": {"email_normalized_sha256": zero_hash, "user_id": str(uuid4()), "workspace_id": str(uuid4()), "membership_id": str(uuid4())}, "catalog": {"path": "shared/apps.json", "sha256_before": zero_hash, "manifest_checksum_after": zero_hash}, "artifacts": artifacts, "audits": {"actor_orphans": 0, "workspace_orphans": 0, "publication_note_orphans": 0, "asset_reference_orphans": 0, "allowed_snapshot_orphans": []}, "compatibility": {"canonical_smoke": "pending", "legacy_api_smoke": "pending", "public_alias_smoke": "pending"}, "cutover": {"maintenance_lock": True, "write_fence": False, "bundle_verified": False, "state": "pending", "steps": []}}


def test_manifest_atomic_round_trip_and_strict_state_transitions(tmp_path):
    manifest = _manifest()
    path = tmp_path / "evidence" / "migration-manifest.json"
    write_manifest(path, manifest)
    loaded = read_manifest(path)
    applied = advance_artifact(loaded, "identity", "applied")
    assert applied["artifacts"]["identity"]["state"] == "applied"
    assert loaded["artifacts"]["identity"]["state"] == "pending"
    with pytest.raises(ManifestError, match="invalid artifact state transition"):
        advance_artifact(applied, "identity", "promoted")


def test_manifest_rejects_promotion_before_ready_cutover(tmp_path):
    manifest = _manifest()
    manifest["artifacts"]["identity"]["state"] = "promoted"
    manifest["cutover"]["steps"] = ["identity"]
    with pytest.raises(ManifestError, match="requires a ready cutover"):
        write_manifest(tmp_path / "early-promotion.json", manifest)


def test_manifest_validates_allowed_snapshot_orphan_evidence(tmp_path):
    valid = _manifest()
    valid["audits"]["allowed_snapshot_orphans"] = [{"resource_type": "note_source", "resource_id": "42", "count": 1, "reason": "deleted source retained by immutable publication snapshot"}]
    write_manifest(tmp_path / "valid-orphan.json", valid)
    invalid = _manifest()
    invalid["audits"]["allowed_snapshot_orphans"] = [{"resource_type": "note_source", "resource_id": "42", "count": 1, "reason": "ok", "content": "secret"}]
    with pytest.raises(ManifestError, match="unknown keys"):
        write_manifest(tmp_path / "secret-orphan.json", invalid)

    for forbidden_type in ("actor", "workspace", "publication", "asset"):
        forbidden = _manifest()
        forbidden["audits"]["allowed_snapshot_orphans"] = [{"resource_type": forbidden_type, "resource_id": "42", "count": 1, "reason": "not an approved snapshot exception"}]
        with pytest.raises(ManifestError, match="not an allowed snapshot source category"):
            write_manifest(tmp_path / f"forbidden-{forbidden_type}.json", forbidden)


def test_manifest_rejects_unknown_version_and_release_mismatch(tmp_path):
    base = _manifest()
    base["manifest_version"] = 2
    with pytest.raises(ManifestError, match="unknown"):
        write_manifest(tmp_path / "manifest.json", base)
    base["manifest_version"] = 1
    base["artifacts"]["notes"]["release_id"] = str(uuid4())
    with pytest.raises(ManifestError, match="mismatched"):
        write_manifest(tmp_path / "manifest.json", base)


def test_manifest_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / "migration-manifest.json"
    initial = _manifest()
    write_manifest(path, initial)
    updated = advance_artifact(initial, "notes", "applied")

    def fail_replace(*_args):
        raise OSError("simulated replace interruption")

    monkeypatch.setattr("shared.migrations.manifest.os.replace", fail_replace)
    with pytest.raises(OSError, match="simulated replace interruption"):
        write_manifest(path, updated)

    assert read_manifest(path) == initial
    assert list(tmp_path.glob(".*.tmp")) == []


def test_manifest_rejects_missing_keys_duplicate_paths_and_duplicate_json_keys(tmp_path):
    manifest = _manifest()
    del manifest["source"]
    with pytest.raises(ManifestError, match="missing required keys"):
        write_manifest(tmp_path / "missing.json", manifest)
    manifest = _manifest()
    manifest["artifacts"]["notes"]["target_path"] = manifest["artifacts"]["calendar"]["target_path"]
    with pytest.raises(ManifestError, match="duplicate paths"):
        write_manifest(tmp_path / "paths.json", manifest)
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"manifest_version":1,"manifest_version":1}', encoding="utf-8")
    with pytest.raises(ManifestError, match="duplicate key"):
        read_manifest(duplicate)


def test_manifest_rejects_unknown_keys_same_artifact_staging_target_and_state_skip(tmp_path):
    manifest = _manifest()
    manifest["unexpected"] = True
    with pytest.raises(ManifestError, match="unknown keys"):
        write_manifest(tmp_path / "unknown-root.json", manifest)
    manifest = _manifest()
    manifest["source"]["unexpected"] = True
    with pytest.raises(ManifestError, match="source has unknown keys"):
        write_manifest(tmp_path / "unknown-nested.json", manifest)
    manifest = _manifest()
    manifest["artifacts"]["notes"]["staging_path"] = manifest["artifacts"]["notes"]["target_path"]
    with pytest.raises(ManifestError, match="staging_path must differ"):
        write_manifest(tmp_path / "same-path.json", manifest)
    path = tmp_path / "transition.json"
    initial = _manifest()
    write_manifest(path, initial)
    skipped = _manifest()
    skipped["release_id"] = initial["release_id"]
    skipped["bootstrap"] = initial["bootstrap"]
    for name, artifact in skipped["artifacts"].items():
        artifact["release_id"] = initial["release_id"]
        if name == "notes": artifact["state"] = "promoted"
    with pytest.raises(ManifestError, match="pending -> promoted|requires a ready cutover"):
        write_manifest(path, skipped)


def test_manifest_freezes_release_inputs_and_validates_cutover_transitions(tmp_path):
    manifest = _manifest()
    manifest["artifacts"]["notes"]["staging_path"] = manifest["artifacts"]["notes"]["source_path"]
    with pytest.raises(ManifestError, match="staging_path must differ from source_path"):
        write_manifest(tmp_path / "source-staging.json", manifest)
    invalid = _manifest()
    invalid["cutover"]["state"] = "mystery"
    with pytest.raises(ManifestError, match="cutover fields"):
        write_manifest(tmp_path / "invalid-cutover.json", invalid)
    path = tmp_path / "immutable.json"
    initial = _manifest()
    write_manifest(path, initial)
    changed = deepcopy(initial)
    changed["source"]["application_version"] = "different"
    with pytest.raises(ManifestError, match="source is immutable"):
        write_manifest(path, changed)
    changed = deepcopy(initial)
    changed["artifacts"]["notes"]["before"]["bytes"] = 99
    with pytest.raises(ManifestError, match="before is immutable"):
        write_manifest(path, changed)
    changed = deepcopy(initial)
    changed["cutover"]["state"] = "rolled_back"
    with pytest.raises(ManifestError, match="pending -> rolled_back"):
        write_manifest(path, changed)


def test_manifest_cutover_readiness_completion_and_evidence_lifecycle(tmp_path):
    path = tmp_path / "lifecycle.json"
    pending = _manifest()
    write_manifest(path, pending)

    applied = deepcopy(pending)
    for artifact in applied["artifacts"].values():
        artifact["state"] = "applied"
    applied["audits"]["actor_orphans"] = 2
    applied["compatibility"]["canonical_smoke"] = "passed"
    write_manifest(path, applied)

    ready = deepcopy(applied)
    for artifact in ready["artifacts"].values():
        artifact["state"] = "verified"
    ready["audits"]["actor_orphans"] = 0
    for key in ready["compatibility"]:
        ready["compatibility"][key] = "passed"
    ready["cutover"].update({"state": "ready", "maintenance_lock": True, "write_fence": True, "bundle_verified": True})
    write_manifest(path, ready)

    regressed = deepcopy(ready)
    regressed["cutover"]["write_fence"] = False
    with pytest.raises(ManifestError, match="ready cutover requires"):
        write_manifest(path, regressed)
    regressed = deepcopy(ready)
    regressed["cutover"]["state"] = "rollback_required"
    regressed["compatibility"]["canonical_smoke"] = "pending"
    with pytest.raises(ManifestError, match="immutable once passed"):
        write_manifest(path, regressed)
    regressed = deepcopy(ready)
    regressed["cutover"]["state"] = "rollback_required"
    regressed["audits"]["actor_orphans"] = 1
    with pytest.raises(ManifestError, match="audits are immutable"):
        write_manifest(path, regressed)

    batched = deepcopy(ready)
    for name in ("identity", "calendar"):
        batched["artifacts"][name]["state"] = "promoted"
        batched["cutover"]["steps"].append(name)
    with pytest.raises(ManifestError, match="only one artifact"):
        write_manifest(path, batched)

    promoted = ready
    for name in ("identity", "calendar", "notes", "publishing", "assets", "publication_types", "asset_blobs"):
        next_prefix = deepcopy(promoted)
        next_prefix["artifacts"][name]["state"] = "promoted"
        next_prefix["cutover"]["steps"].append(name)
        write_manifest(path, next_prefix)
        promoted = next_prefix
    complete = deepcopy(promoted)
    complete["cutover"].update({"state": "complete", "maintenance_lock": False, "write_fence": False})
    write_manifest(path, complete)
    assert read_manifest(path)["cutover"]["state"] == "complete"


def test_manifest_rejects_ready_or_complete_without_required_evidence(tmp_path):
    ready = _manifest()
    ready["cutover"].update({"state": "ready", "write_fence": True, "bundle_verified": True})
    with pytest.raises(ManifestError, match="verified/promoted prefix"):
        write_manifest(tmp_path / "unused-ready-manifest.json", ready)
    complete = _manifest()
    for artifact in complete["artifacts"].values():
        artifact["state"] = "promoted"
    for key in complete["compatibility"]:
        complete["compatibility"][key] = "passed"
    complete["cutover"].update({"state": "complete", "maintenance_lock": False, "write_fence": False, "bundle_verified": True, "steps": []})
    with pytest.raises(ManifestError, match="full ordered steps"):
        write_manifest(tmp_path / "unused-complete-manifest.json", complete)


def test_manifest_rejects_invalid_versions_missing_before_and_bad_step_order(tmp_path):
    invalid = _manifest()
    invalid["artifacts"]["calendar"]["from_version"] = 3
    invalid["artifacts"]["calendar"]["to_version"] = 2
    with pytest.raises(ManifestError, match="invalid version transition"):
        write_manifest(tmp_path / "downgrade.json", invalid)
    invalid = _manifest()
    invalid["artifacts"]["notes"]["before"] = None
    with pytest.raises(ManifestError, match="before evidence is required"):
        write_manifest(tmp_path / "before.json", invalid)
    invalid = _manifest()
    invalid["artifacts"]["calendar"]["state"] = "promoted"
    invalid["cutover"]["steps"] = ["calendar"]
    with pytest.raises(ManifestError, match="exact ordered promotion prefix"):
        write_manifest(tmp_path / "order.json", invalid)
    invalid = _manifest()
    invalid["artifacts"]["identity"]["state"] = "promoted"
    invalid["cutover"]["steps"] = ["identity", "identity"]
    with pytest.raises(ManifestError, match="exact ordered promotion prefix"):
        write_manifest(tmp_path / "duplicate-step.json", invalid)


def test_manifest_rollback_states_preserve_promotion_journal(tmp_path):
    path = tmp_path / "rollback-journal.json"
    ready = _manifest()
    for artifact in ready["artifacts"].values():
        artifact["state"] = "verified"
    for key in ready["compatibility"]:
        ready["compatibility"][key] = "passed"
    ready["cutover"].update({"state": "ready", "maintenance_lock": True, "write_fence": True, "bundle_verified": True})
    write_manifest(path, ready)

    invalid = deepcopy(ready)
    invalid["cutover"]["state"] = "rollback_required"
    invalid["artifacts"]["identity"]["state"] = "promoted"
    invalid["cutover"]["steps"] = ["identity"]
    with pytest.raises(ManifestError, match="preserve the promotion journal"):
        write_manifest(path, invalid)

    rollback_required = deepcopy(ready)
    rollback_required["cutover"]["state"] = "rollback_required"
    write_manifest(path, rollback_required)
    rolled_back = deepcopy(rollback_required)
    rolled_back["cutover"]["state"] = "rolled_back"
    write_manifest(path, rolled_back)
    invalid = deepcopy(rolled_back)
    invalid["artifacts"]["identity"]["state"] = "promoted"
    invalid["cutover"]["steps"] = ["identity"]
    with pytest.raises(ManifestError, match="preserve the promotion journal"):
        write_manifest(path, invalid)
