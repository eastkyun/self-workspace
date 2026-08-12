"""Strict and durable coordinated-migration manifest storage."""

from __future__ import annotations

import json
import os
import re
import tempfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Mapping
from uuid import UUID

MANIFEST_VERSION = 1
ARTIFACT_STATES = ("pending", "applied", "verified", "promoted")
CUTOVER_STATES = ("pending", "ready", "rollback_required", "rolled_back", "complete")
COMPATIBILITY_STATES = ("pending", "passed", "failed")
ARTIFACT_NAMES = {
    "identity", "calendar", "notes", "publishing", "assets",
    "publication_types", "asset_blobs",
}
PROMOTION_ORDER = ("identity", "calendar", "notes", "publishing", "assets", "publication_types", "asset_blobs")
VERSION_TRANSITIONS = {
    "identity": (0, 5), "calendar": (2, 3), "notes": (1, 2),
    "publishing": (1, 2), "assets": (1, 2),
    "publication_types": (1, 1), "asset_blobs": (1, 1),
}
DB_ARTIFACTS = {"identity", "calendar", "notes", "publishing", "assets"}
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
ORPHAN_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class ManifestError(RuntimeError):
    pass


def _require_keys(value: Mapping[str, object], keys: set[str], label: str) -> None:
    missing = keys - value.keys()
    if missing:
        raise ManifestError(f"{label} missing required keys: {', '.join(sorted(missing))}")
    unknown = value.keys() - keys
    if unknown:
        raise ManifestError(f"{label} has unknown keys: {', '.join(sorted(unknown))}")


def _uuid(value: object, label: str) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except ValueError as error:
        raise ManifestError(f"{label} must be a canonical UUID") from error
    return value


def _hash(value: object, label: str) -> None:
    if not isinstance(value, str) or SHA256.fullmatch(value) is None:
        raise ManifestError(f"{label} must be a lowercase SHA-256")


def _path(value: object, label: str, *, nullable: bool = False, relative: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value or "\\" in value:
        raise ManifestError(f"{label} must be a normalized path")
    pure = PurePosixPath(value)
    if ".." in pure.parts or str(pure) != value or (relative and pure.is_absolute()):
        raise ManifestError(f"{label} must be a normalized path")
    return value


def _evidence(value: object, label: str, kind: str) -> None:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be an object")
    if kind == "db":
        keys = {"bytes", "file_sha256", "schema_sha256", "content_sha256", "user_version", "rows", "quick_check", "foreign_key_orphans"}
        _require_keys(value, keys, label)
        for key in ("file_sha256", "schema_sha256", "content_sha256"):
            _hash(value[key], f"{label}.{key}")
        rows = value["rows"]
        if not isinstance(rows, dict) or any(not isinstance(k, str) or not isinstance(v, int) or isinstance(v, bool) or v < 0 for k, v in rows.items()):
            raise ManifestError(f"{label}.rows must contain non-negative counts")
        if value["quick_check"] != "ok":
            raise ManifestError(f"{label}.quick_check must be ok")
        numeric = ("bytes", "user_version", "foreign_key_orphans")
    elif kind == "file":
        _require_keys(value, {"bytes", "sha256"}, label)
        _hash(value["sha256"], f"{label}.sha256")
        numeric = ("bytes",)
    else:
        _require_keys(value, {"files", "manifest_sha256"}, label)
        _hash(value["manifest_sha256"], f"{label}.manifest_sha256")
        files = value["files"]
        if not isinstance(files, list):
            raise ManifestError(f"{label}.files must be a list")
        paths = []
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                raise ManifestError(f"{label}.files[{index}] must be an object")
            _require_keys(item, {"path", "bytes", "sha256"}, f"{label}.files[{index}]")
            paths.append(_path(item["path"], f"{label}.files[{index}].path"))
            _hash(item["sha256"], f"{label}.files[{index}].sha256")
            if not isinstance(item["bytes"], int) or isinstance(item["bytes"], bool) or item["bytes"] < 0:
                raise ManifestError(f"{label}.files[{index}].bytes must be non-negative")
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ManifestError(f"{label}.files paths must be unique and sorted")
        numeric = ()
    for key in numeric:
        if not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] < 0:
            raise ManifestError(f"{label}.{key} must be a non-negative integer")


def validate_manifest(manifest: Mapping[str, object]) -> None:
    required = {"manifest_version", "release_id", "created_at", "source", "bootstrap", "catalog", "artifacts", "audits", "compatibility", "cutover"}
    _require_keys(manifest, required, "manifest")
    if manifest["manifest_version"] != MANIFEST_VERSION:
        raise ManifestError("unknown migration manifest version")
    release_id = _uuid(manifest["release_id"], "manifest release_id")
    created_at = manifest["created_at"]
    try:
        if not isinstance(created_at, str) or not created_at.endswith("Z"):
            raise ValueError
        datetime.fromisoformat(created_at.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise ManifestError("created_at must be RFC3339 UTC") from error

    source = manifest["source"]
    if not isinstance(source, dict): raise ManifestError("source must be an object")
    _require_keys(source, {"git_commit", "git_branch", "worktree_porcelain_sha256", "application_version", "single_worker_asserted"}, "source")
    if not isinstance(source["git_commit"], str) or GIT_COMMIT.fullmatch(source["git_commit"]) is None: raise ManifestError("source.git_commit must be 40-hex")
    _hash(source["worktree_porcelain_sha256"], "source.worktree_porcelain_sha256")
    if not isinstance(source["git_branch"], str) or not source["git_branch"] or not isinstance(source["application_version"], str) or not source["application_version"]: raise ManifestError("source strings must be non-empty")
    if source["single_worker_asserted"] is not True: raise ManifestError("source.single_worker_asserted must be true")

    bootstrap = manifest["bootstrap"]
    if not isinstance(bootstrap, dict): raise ManifestError("bootstrap must be an object")
    _require_keys(bootstrap, {"email_normalized_sha256", "user_id", "workspace_id", "membership_id"}, "bootstrap")
    _hash(bootstrap["email_normalized_sha256"], "bootstrap.email_normalized_sha256")
    for key in ("user_id", "workspace_id", "membership_id"): _uuid(bootstrap[key], f"bootstrap.{key}")

    catalog = manifest["catalog"]
    if not isinstance(catalog, dict): raise ManifestError("catalog must be an object")
    _require_keys(catalog, {"path", "sha256_before", "manifest_checksum_after"}, "catalog")
    paths = [(_path(catalog["path"], "catalog.path", relative=True), "catalog")]
    _hash(catalog["sha256_before"], "catalog.sha256_before"); _hash(catalog["manifest_checksum_after"], "catalog.manifest_checksum_after")

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, dict) or set(artifacts) != ARTIFACT_NAMES: raise ManifestError("artifacts must contain the fixed inventory")
    for name, artifact in artifacts.items():
        if not isinstance(artifact, dict): raise ManifestError(f"artifact {name} must be an object")
        _require_keys(artifact, {"source_path", "staging_path", "target_path", "from_version", "to_version", "release_id", "state", "before", "after"}, f"artifact {name}")
        if artifact["release_id"] != release_id: raise ManifestError(f"artifact {name} has a mismatched release_id")
        if artifact["state"] not in ARTIFACT_STATES: raise ManifestError(f"artifact {name} has an invalid state")
        for key in ("from_version", "to_version"):
            if not isinstance(artifact[key], int) or isinstance(artifact[key], bool) or artifact[key] < 0: raise ManifestError(f"artifact {name}.{key} must be non-negative")
        if (artifact["from_version"], artifact["to_version"]) != VERSION_TRANSITIONS[name]: raise ManifestError(f"artifact {name} has an invalid version transition")
        source_path = _path(artifact["source_path"], f"artifact {name}.source_path", nullable=True)
        staging_path = _path(artifact["staging_path"], f"artifact {name}.staging_path", relative=True)
        target_path = _path(artifact["target_path"], f"artifact {name}.target_path")
        if staging_path == target_path: raise ManifestError(f"artifact {name} staging_path must differ from target_path")
        if source_path is not None and staging_path == source_path: raise ManifestError(f"artifact {name} staging_path must differ from source_path")
        paths.extend([(source_path, name), (staging_path, name), (target_path, name)])
        kind = "db" if name in DB_ARTIFACTS else "file" if name == "publication_types" else "blob"
        if source_path is not None and artifact["before"] is None: raise ManifestError(f"artifact {name}.before evidence is required for a source artifact")
        if artifact["before"] is not None: _evidence(artifact["before"], f"artifact {name}.before", kind)
        if artifact["after"] is not None: _evidence(artifact["after"], f"artifact {name}.after", kind)
        elif artifact["state"] in {"verified", "promoted"}: raise ManifestError(f"artifact {name}.after evidence is required")
        if kind == "db" and artifact["before"] is not None and artifact["before"]["user_version"] != artifact["from_version"]: raise ManifestError(f"artifact {name}.before user_version mismatch")
        if kind == "db" and artifact["after"] is not None and artifact["after"]["user_version"] != artifact["to_version"]: raise ManifestError(f"artifact {name}.after user_version mismatch")
    path_owners: dict[str, str] = {}
    for path, owner in paths:
        if path is not None and path in path_owners and path_owners[path] != owner: raise ManifestError("manifest contains duplicate paths")
        if path is not None: path_owners[path] = owner

    audits = manifest["audits"]
    if not isinstance(audits, dict): raise ManifestError("audits must be an object")
    _require_keys(audits, {"actor_orphans", "workspace_orphans", "publication_note_orphans", "asset_reference_orphans", "allowed_snapshot_orphans"}, "audits")
    for key in ("actor_orphans", "workspace_orphans", "publication_note_orphans", "asset_reference_orphans"):
        if not isinstance(audits[key], int) or isinstance(audits[key], bool) or audits[key] < 0: raise ManifestError(f"audits.{key} must be non-negative")
    allowed_orphans = audits["allowed_snapshot_orphans"]
    if not isinstance(allowed_orphans, list): raise ManifestError("audits.allowed_snapshot_orphans must be a list")
    orphan_keys: list[tuple[str, str]] = []
    for index, orphan in enumerate(allowed_orphans):
        label = f"audits.allowed_snapshot_orphans[{index}]"
        if not isinstance(orphan, dict): raise ManifestError(f"{label} must be an object")
        _require_keys(orphan, {"resource_type", "resource_id", "count", "reason"}, label)
        for key in ("resource_type", "resource_id"):
            if not isinstance(orphan[key], str) or ORPHAN_REFERENCE.fullmatch(orphan[key]) is None: raise ManifestError(f"{label}.{key} must be an identifier-safe reference")
        if orphan["resource_type"] not in {"note_source", "calendar_source"}: raise ManifestError(f"{label}.resource_type is not an allowed snapshot source category")
        if not isinstance(orphan["count"], int) or isinstance(orphan["count"], bool) or orphan["count"] < 1: raise ManifestError(f"{label}.count must be a positive integer")
        if not isinstance(orphan["reason"], str) or not orphan["reason"].strip() or len(orphan["reason"]) > 256: raise ManifestError(f"{label}.reason must be a non-empty bounded string")
        orphan_keys.append((orphan["resource_type"], orphan["resource_id"]))
    if orphan_keys != sorted(orphan_keys) or len(orphan_keys) != len(set(orphan_keys)): raise ManifestError("allowed snapshot orphans must be unique and sorted")
    compatibility = manifest["compatibility"]
    if not isinstance(compatibility, dict): raise ManifestError("compatibility must be an object")
    _require_keys(compatibility, {"canonical_smoke", "legacy_api_smoke", "public_alias_smoke"}, "compatibility")
    if any(compatibility[key] not in COMPATIBILITY_STATES for key in compatibility): raise ManifestError("compatibility fields have invalid states")
    cutover = manifest["cutover"]
    if not isinstance(cutover, dict): raise ManifestError("cutover must be an object")
    _require_keys(cutover, {"maintenance_lock", "write_fence", "bundle_verified", "state", "steps"}, "cutover")
    if any(not isinstance(cutover[key], bool) for key in ("maintenance_lock", "write_fence", "bundle_verified")) or cutover["state"] not in CUTOVER_STATES or not isinstance(cutover["steps"], list): raise ManifestError("cutover fields have invalid types")
    steps = cutover["steps"]
    if steps != list(PROMOTION_ORDER[:len(steps)]): raise ManifestError("cutover steps must be the exact ordered promotion prefix")
    if steps and cutover["state"] not in {"ready", "rollback_required", "rolled_back", "complete"}: raise ManifestError("artifact promotion requires a ready cutover")
    if cutover["state"] == "pending" and any(artifact["state"] == "promoted" for artifact in artifacts.values()): raise ManifestError("artifact promotion requires a ready cutover")
    for index, name in enumerate(PROMOTION_ORDER):
        if index < len(steps) and artifacts[name]["state"] != "promoted": raise ManifestError(f"cutover step {name} requires a promoted artifact")
    if cutover["state"] == "ready":
        if not cutover["maintenance_lock"] or not cutover["write_fence"] or not cutover["bundle_verified"]: raise ManifestError("ready cutover requires maintenance lock, write fence, and verified bundle")
        for index, name in enumerate(PROMOTION_ORDER):
            expected = "promoted" if index < len(steps) else "verified"
            if artifacts[name]["state"] != expected: raise ManifestError("ready cutover requires a deterministic verified/promoted prefix")
        if any(compatibility[key] != "passed" for key in compatibility): raise ManifestError("ready cutover requires passed compatibility checks")
        if any(audits[key] != 0 for key in ("actor_orphans", "workspace_orphans", "publication_note_orphans", "asset_reference_orphans")): raise ManifestError("ready cutover requires zero unexpected orphans")
    if cutover["state"] == "complete":
        if cutover["maintenance_lock"] or cutover["write_fence"] or not cutover["bundle_verified"] or steps != list(PROMOTION_ORDER): raise ManifestError("complete cutover requires released fences, verified bundle, and full ordered steps")
        if any(artifact["state"] != "promoted" for artifact in artifacts.values()): raise ManifestError("complete cutover requires all artifacts promoted")
        if any(compatibility[key] != "passed" for key in compatibility): raise ManifestError("complete cutover requires passed compatibility checks")


def advance_artifact(manifest: Mapping[str, object], artifact_name: str, new_state: str) -> dict[str, object]:
    validate_manifest(manifest); updated = deepcopy(dict(manifest)); artifacts = updated["artifacts"]; assert isinstance(artifacts, dict); artifact = artifacts.get(artifact_name)
    if not isinstance(artifact, dict): raise ManifestError(f"unknown manifest artifact: {artifact_name}")
    old_state = artifact["state"]
    if new_state not in ARTIFACT_STATES or ARTIFACT_STATES.index(new_state) != ARTIFACT_STATES.index(old_state) + 1: raise ManifestError(f"invalid artifact state transition: {old_state} -> {new_state}")
    artifact["state"] = new_state; validate_manifest(updated); return updated


def _validate_replacement(previous: Mapping[str, object], current: Mapping[str, object]) -> None:
    if previous["release_id"] != current["release_id"]: raise ManifestError("cannot replace a manifest with a different release_id")
    for key in ("manifest_version", "release_id", "created_at", "source", "bootstrap", "catalog"):
        if previous[key] != current[key]: raise ManifestError(f"manifest {key} is immutable for a release")
    previous_artifacts = previous["artifacts"]; current_artifacts = current["artifacts"]
    assert isinstance(previous_artifacts, dict) and isinstance(current_artifacts, dict)
    for name in ARTIFACT_NAMES:
        old = previous_artifacts[name]; new = current_artifacts[name]
        assert isinstance(old, dict) and isinstance(new, dict)
        old_index = ARTIFACT_STATES.index(old["state"]); new_index = ARTIFACT_STATES.index(new["state"])
        if new_index not in {old_index, old_index + 1}: raise ManifestError(f"invalid artifact state transition: {old['state']} -> {new['state']}")
        for key in ("source_path", "staging_path", "target_path", "from_version", "to_version", "release_id", "before"):
            if old[key] != new[key]: raise ManifestError(f"artifact {name}.{key} is immutable")
        if old["after"] is not None and old["after"] != new["after"]: raise ManifestError(f"artifact {name}.after evidence cannot be replaced")
        if old_index >= ARTIFACT_STATES.index("verified"):
            old_without_state = {key: value for key, value in old.items() if key != "state"}; new_without_state = {key: value for key, value in new.items() if key != "state"}
            if old_without_state != new_without_state: raise ManifestError(f"verified artifact {name} is immutable")
    previous_cutover = previous["cutover"]; current_cutover = current["cutover"]
    assert isinstance(previous_cutover, dict) and isinstance(current_cutover, dict)
    old_cutover = previous_cutover["state"]; new_cutover = current_cutover["state"]
    allowed = {
        "pending": {"pending", "ready", "rollback_required"},
        "ready": {"ready", "complete", "rollback_required"},
        "rollback_required": {"rollback_required", "rolled_back"},
        "rolled_back": {"rolled_back"},
        "complete": {"complete"},
    }
    if new_cutover not in allowed[old_cutover]: raise ManifestError(f"invalid cutover state transition: {old_cutover} -> {new_cutover}")
    old_steps = previous_cutover["steps"]; new_steps = current_cutover["steps"]
    if new_steps[:len(old_steps)] != old_steps: raise ManifestError("cutover steps must be append-only")
    rollback_states = {"rollback_required", "rolled_back"}
    if old_cutover in rollback_states or new_cutover in rollback_states:
        if new_steps != old_steps or previous_artifacts != current_artifacts: raise ManifestError("rollback states preserve the promotion journal and artifact states")
    if len(new_steps) > len(old_steps) + 1: raise ManifestError("only one artifact may be promoted per manifest write")
    if len(new_steps) == len(old_steps) + 1:
        if old_cutover != "ready" or new_cutover != "ready": raise ManifestError("promotion steps may be appended only while cutover is ready")
        promoted_name = new_steps[-1]
        old_promoted = previous_artifacts[promoted_name]; new_promoted = current_artifacts[promoted_name]
        assert isinstance(old_promoted, dict) and isinstance(new_promoted, dict)
        if old_promoted["state"] != "verified" or new_promoted["state"] != "promoted": raise ManifestError("cutover step must record its verified to promoted transition")
    if new_cutover == "complete" and old_cutover != "complete" and old_steps != list(PROMOTION_ORDER): raise ManifestError("full promotion sequence must be fsynced before complete")
    if old_cutover == "ready" and new_cutover == "ready":
        for key in ("maintenance_lock", "write_fence", "bundle_verified"):
            if previous_cutover[key] and not current_cutover[key]: raise ManifestError(f"ready cutover flag {key} cannot regress")
    if previous_cutover["bundle_verified"] and not current_cutover["bundle_verified"]: raise ManifestError("cutover bundle_verified cannot regress")
    previous_compatibility = previous["compatibility"]; current_compatibility = current["compatibility"]
    assert isinstance(previous_compatibility, dict) and isinstance(current_compatibility, dict)
    for key, old_state in previous_compatibility.items():
        new_state = current_compatibility[key]
        if old_state == "passed" and new_state != old_state: raise ManifestError(f"compatibility {key} is immutable once passed")
        if old_state == "failed" and new_state == "pending": raise ManifestError(f"compatibility {key} cannot return to pending")
    if old_cutover in {"ready", "rollback_required", "rolled_back", "complete"} and previous["audits"] != current["audits"]: raise ManifestError("audits are immutable after cutover readiness")


def write_manifest(path: Path, manifest: Mapping[str, object]) -> None:
    validate_manifest(manifest); path = Path(path)
    if path.exists(): _validate_replacement(read_manifest(path), manifest)
    path.parent.mkdir(parents=True, exist_ok=True); payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"; descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent); temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle: handle.write(payload); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary_path, path); directory_fd = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory_fd)
        finally: os.close(directory_fd)
    finally: temporary_path.unlink(missing_ok=True)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result: raise ManifestError(f"migration manifest contains duplicate key: {key}")
        result[key] = value
    return result


def read_manifest(path: Path) -> dict[str, object]:
    try: raw = json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    except (OSError, json.JSONDecodeError) as error: raise ManifestError("migration manifest cannot be read") from error
    if not isinstance(raw, dict): raise ManifestError("migration manifest root must be an object")
    validate_manifest(raw); return raw
