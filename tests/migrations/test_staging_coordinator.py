from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
import tools.staging_migrations as staging_module

from shared.migrations import read_manifest, write_manifest
from tests.migrations.test_domain_migrations import _seed_sources
from tools.staging_migrations import (
    StagingMigrationError,
    _blob_evidence,
    _file_evidence,
    database_evidence,
    prepare_cutover,
    stage_domains,
    verify_staging,
)


def _artifact(release_id, source, staging, target, old, new, before, *, state="pending", after=None):
    return {"source_path": str(source) if source is not None else None, "staging_path": staging, "target_path": str(target), "from_version": old, "to_version": new, "release_id": release_id, "state": state, "before": before, "after": after}


def _manifest(
    root: Path, *, allowed_orphan: bool = False, allowed_note_orphan: bool = False,
    invalid_asset_field: bool = False, empty_blobs: bool = False,
) -> Path:
    sources = _seed_sources(root / "sources")
    if allowed_orphan:
        with sqlite3.connect(sources["calendar"]) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("DELETE FROM tasks WHERE id=1")
    if allowed_note_orphan:
        with sqlite3.connect(sources["notes"]) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("DELETE FROM notes WHERE id=1")
    if invalid_asset_field:
        with sqlite3.connect(sources["assets"]) as connection:
            connection.execute("UPDATE asset_references SET field='not-a-real-source-field'")
    release = str(uuid4()); user = str(uuid4()); workspace = str(uuid4()); member = str(uuid4())
    identity = root / "identity.db"
    with sqlite3.connect(identity) as connection:
        connection.executescript("CREATE TABLE users(id TEXT PRIMARY KEY,status TEXT); CREATE TABLE workspaces(id TEXT PRIMARY KEY,status TEXT); CREATE TABLE workspace_members(id TEXT PRIMARY KEY,workspace_id TEXT,user_id TEXT,status TEXT);")
        connection.execute("INSERT INTO users VALUES(?,'ACTIVE')", (user,))
        connection.execute("INSERT INTO workspaces VALUES(?,'ACTIVE')", (workspace,))
        connection.execute("INSERT INTO workspace_members VALUES(?,?,?,'ACTIVE')", (member, workspace, user))
        connection.execute("PRAGMA user_version=4")
    types = root / "types.json"; types.write_text('{"types":[]}\n')
    blobs = root / "blobs"; blobs.mkdir()
    if not empty_blobs:
        (blobs / "aa.bin").write_bytes(b"blob-bytes")
    artifacts = {
        "identity": _artifact(release, None, f".omx/releases/{release}/staging/identity", identity, 0, 4, None, state="verified", after=database_evidence(identity)),
        "calendar": _artifact(release, sources["calendar"], f".omx/releases/{release}/staging/calendar", sources["calendar"], 2, 3, database_evidence(sources["calendar"])),
        "notes": _artifact(release, sources["notes"], f".omx/releases/{release}/staging/notes", sources["notes"], 1, 2, database_evidence(sources["notes"])),
        "publishing": _artifact(release, sources["publishing"], f".omx/releases/{release}/staging/publishing", sources["publishing"], 1, 2, database_evidence(sources["publishing"])),
        "assets": _artifact(release, sources["assets"], f".omx/releases/{release}/staging/assets", sources["assets"], 1, 2, database_evidence(sources["assets"])),
        "publication_types": _artifact(release, types, f".omx/releases/{release}/staging/publication_types", types, 1, 1, _file_evidence(types)),
        "asset_blobs": _artifact(release, blobs, f".omx/releases/{release}/staging/asset_blobs", blobs, 1, 1, _blob_evidence(blobs)),
    }
    manifest = {
        "manifest_version": 1, "release_id": release, "created_at": "2026-08-03T00:00:00Z",
        "source": {"git_commit": "0" * 40, "git_branch": "test", "worktree_porcelain_sha256": "0" * 64, "application_version": "test", "single_worker_asserted": True},
        "bootstrap": {"email_normalized_sha256": "0" * 64, "user_id": user, "workspace_id": workspace, "membership_id": member},
        "catalog": {"path": "shared/apps.json", "sha256_before": "0" * 64, "manifest_checksum_after": "0" * 64},
        "artifacts": artifacts,
        "audits": {"actor_orphans": 0, "workspace_orphans": 0, "publication_note_orphans": 0, "asset_reference_orphans": 0, "allowed_snapshot_orphans": ([{"resource_type": "calendar_source", "resource_id": "1", "count": 1, "reason": "legacy task was intentionally deleted"}] if allowed_orphan else [{"resource_type": "note_source", "resource_id": "1", "count": 1, "reason": "published snapshot preserves an intentionally deleted note"}] if allowed_note_orphan else [])},
        "compatibility": {"canonical_smoke": "pending", "legacy_api_smoke": "pending", "public_alias_smoke": "pending"},
        "cutover": {"maintenance_lock": False, "write_fence": False, "bundle_verified": False, "state": "pending", "steps": []},
    }
    path = root / "migration-manifest.json"; write_manifest(path, manifest); return path


def _source_hashes(manifest_path: Path):
    manifest = read_manifest(manifest_path)
    return {name: hashlib.sha256(Path(artifact["source_path"]).read_bytes()).hexdigest() for name, artifact in manifest["artifacts"].items() if name in {"calendar", "notes", "publishing", "assets"}}


def test_stage_verify_and_cutover_plan_preserve_every_operational_artifact(tmp_path):
    manifest_path = _manifest(tmp_path); before = _source_hashes(manifest_path)
    staged = stage_domains(manifest_path, workspace_root=tmp_path)
    assert all(staged["artifacts"][name]["state"] == "verified" for name in ("calendar", "notes", "publishing", "assets", "publication_types", "asset_blobs"))
    verify_staging(manifest_path, workspace_root=tmp_path)
    cutover_path, rollback_path = prepare_cutover(manifest_path, workspace_root=tmp_path)
    assert _source_hashes(manifest_path) == before
    cutover = json.loads(cutover_path.read_text()); rollback = json.loads(rollback_path.read_text())
    assert cutover["production_promotion_allowed"] is False
    assert cutover["requires_phase6_compatible_binary"] is True
    assert rollback["all_or_nothing"] is True
    assert rollback["production_promotion_allowed"] is False
    assert [item["name"] for item in cutover["ordered_artifacts"]] == ["identity", "calendar", "notes", "publishing", "assets", "publication_types", "asset_blobs"]
    publishing = Path(staged["artifacts"]["publishing"]["staging_path"])
    publishing = tmp_path / publishing
    with sqlite3.connect(publishing) as connection:
        assert connection.execute("SELECT COUNT(*) FROM publication_public_assets").fetchone()[0] == 1


def test_staging_failure_on_active_wal_leaves_all_sources_unchanged(tmp_path):
    manifest_path = _manifest(tmp_path); before = _source_hashes(manifest_path)
    manifest = read_manifest(manifest_path); calendar = Path(manifest["artifacts"]["calendar"]["source_path"])
    Path(f"{calendar}-wal").write_bytes(b"active")
    with pytest.raises(StagingMigrationError, match="not quiescent"):
        stage_domains(manifest_path, workspace_root=tmp_path)
    assert _source_hashes(manifest_path) == before
    assert all(read_manifest(manifest_path)["artifacts"][name]["state"] == "pending" for name in ("calendar", "notes", "publishing", "assets"))


@pytest.mark.parametrize("failure_write", (2, 4))
def test_stage_resumes_applied_artifact_and_verified_prefix(tmp_path, monkeypatch, failure_write):
    manifest_path = _manifest(tmp_path); before = _source_hashes(manifest_path)
    real_write = staging_module.write_manifest
    calls = 0

    def interrupt(path, manifest):
        nonlocal calls
        calls += 1
        if calls == failure_write:
            raise OSError("simulated manifest fsync interruption")
        real_write(path, manifest)

    monkeypatch.setattr(staging_module, "write_manifest", interrupt)
    with pytest.raises(OSError, match="simulated"):
        stage_domains(manifest_path, workspace_root=tmp_path)
    monkeypatch.setattr(staging_module, "write_manifest", real_write)

    resumed = stage_domains(manifest_path, workspace_root=tmp_path)
    assert all(resumed["artifacts"][name]["state"] == "verified" for name in ("calendar", "notes", "publishing", "assets", "publication_types", "asset_blobs"))
    assert _source_hashes(manifest_path) == before


def test_allowed_deleted_source_snapshot_is_counted_and_preserved(tmp_path):
    manifest_path = _manifest(tmp_path, allowed_orphan=True)
    result = stage_domains(manifest_path, workspace_root=tmp_path)
    assert result["audits"]["allowed_snapshot_orphans"] == [{"resource_type": "calendar_source", "resource_id": "1", "count": 1, "reason": "legacy task was intentionally deleted"}]
    verify_staging(manifest_path, workspace_root=tmp_path)


def test_allowed_deleted_publication_note_snapshot_is_not_an_unexpected_orphan(tmp_path):
    manifest_path = _manifest(tmp_path, allowed_note_orphan=True)
    stage_domains(manifest_path, workspace_root=tmp_path)
    verify_staging(manifest_path, workspace_root=tmp_path)


def test_invalid_asset_owner_field_is_an_orphan_and_not_publicly_projected(tmp_path):
    manifest_path = _manifest(tmp_path, invalid_asset_field=True)
    with pytest.raises(StagingMigrationError, match="cross-database audit failed"):
        stage_domains(manifest_path, workspace_root=tmp_path)


def test_missing_empty_blob_backup_blocks_cutover_plan(tmp_path):
    manifest_path = _manifest(tmp_path, empty_blobs=True)
    staged = stage_domains(manifest_path, workspace_root=tmp_path)
    backup = tmp_path / ".omx" / "releases" / staged["release_id"] / "backups" / "asset_blobs"
    backup.rmdir()
    with pytest.raises(StagingMigrationError, match="blob tree is missing"):
        prepare_cutover(manifest_path, workspace_root=tmp_path)


def test_missing_or_tampered_public_id_map_blocks_resume_and_cutover(tmp_path):
    manifest_path = _manifest(tmp_path)
    staged = stage_domains(manifest_path, workspace_root=tmp_path)
    release_root = tmp_path / ".omx" / "releases" / staged["release_id"]
    public_ids = release_root / "public-id-map.json"
    original = json.loads(public_ids.read_text())
    public_ids.unlink()
    with pytest.raises(StagingMigrationError, match="public ID map is missing"):
        stage_domains(manifest_path, workspace_root=tmp_path)
    with pytest.raises(StagingMigrationError, match="public ID map is missing"):
        prepare_cutover(manifest_path, workspace_root=tmp_path)
    tampered = dict(original)
    tampered["publications"] = dict(original["publications"])
    tampered["publications"]["1"] = str(uuid4())
    public_ids.write_text(json.dumps(tampered))
    with pytest.raises(StagingMigrationError, match="does not match staged databases"):
        verify_staging(manifest_path, workspace_root=tmp_path)


def test_interrupted_atomic_copy_is_reconstructed_on_retry(tmp_path, monkeypatch):
    manifest_path = _manifest(tmp_path)
    real_replace = staging_module.os.replace
    interrupted = False
    def fail_copy(source, destination):
        nonlocal interrupted
        if not interrupted and str(source).endswith(".copy"):
            interrupted = True
            raise OSError("simulated copy rename interruption")
        return real_replace(source, destination)
    monkeypatch.setattr(staging_module.os, "replace", fail_copy)
    with pytest.raises(OSError, match="simulated copy"):
        stage_domains(manifest_path, workspace_root=tmp_path)
    monkeypatch.setattr(staging_module.os, "replace", real_replace)
    stage_domains(manifest_path, workspace_root=tmp_path)
    verify_staging(manifest_path, workspace_root=tmp_path)
