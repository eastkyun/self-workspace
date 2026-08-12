"""Coordinated Phase 5 staging of workspace-aware domain databases.

This module deliberately has no promote operation.  It clones operational
artifacts, migrates only those clones, and emits a deterministic cutover plan
and rollback inventory for the later compatible-runtime phase.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Mapping
from uuid import UUID, uuid4

from apps.calendar.migrations import MIGRATIONS as CALENDAR_MIGRATIONS, TARGET_VERSION as CALENDAR_TARGET
from apps.notes.migrations import MIGRATIONS as NOTES_MIGRATIONS, TARGET_VERSION as NOTES_TARGET
from assets.migrations import MIGRATIONS as ASSET_MIGRATIONS, TARGET_VERSION as ASSET_TARGET
from publishing.migrations import MIGRATIONS as PUBLISHING_MIGRATIONS, TARGET_VERSION as PUBLISHING_TARGET
from shared.migrations import migrate, read_manifest, write_manifest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_ORDER = ("calendar", "notes", "publishing", "assets")
COPY_ORDER = ("publication_types", "asset_blobs")
MIGRATIONS = {
    "calendar": (CALENDAR_TARGET, CALENDAR_MIGRATIONS),
    "notes": (NOTES_TARGET, NOTES_MIGRATIONS),
    "publishing": (PUBLISHING_TARGET, PUBLISHING_MIGRATIONS),
    "assets": (ASSET_TARGET, ASSET_MIGRATIONS),
}
PRESERVED_COLUMNS = {
    "calendar": {"tasks": ("id","title","description","status","position","all_day","start_at","due_at","created_at","updated_at"), "topics": ("id","name","color","created_at","updated_at"), "task_topics": ("task_id","topic_id")},
    "notes": {"notes": ("id","title","content","note_type","is_pinned","created_at","updated_at"), "note_links": ("id","note_id","source_app","source_type","source_id","source_title_snapshot","created_at")},
    "publishing": {"publications": ("id","note_id","type_id","slug","title","summary","status","latest_version","published_version","draft_version","published_at","archived_at","created_at","updated_at"), "publication_versions": ("id","publication_id","version","title","summary","markdown","source_note_updated_at","created_at")},
    "assets": {"assets": ("id","sha256","media_type","byte_size","storage_key","original_name","created_at"), "asset_references": ("id","asset_id","owner_type","owner_id","field","created_at")},
}


class StagingMigrationError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve(path: str, root: Path) -> Path:
    candidate = Path(path)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _atomic_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def database_evidence(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise StagingMigrationError(f"database is missing: {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tables = [str(r[0]) for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        schema = [tuple(r) for r in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")]
        content = []
        rows = {}
        for table in tables:
            values = [tuple(r) for r in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
            rows[table] = len(values); content.append((table, values))
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        foreign = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    data = path.read_bytes()
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode()
    return {"bytes": len(data), "file_sha256": _sha256(data), "schema_sha256": _sha256(encode(schema)), "content_sha256": _sha256(encode(content)), "user_version": version, "rows": rows, "quick_check": quick, "foreign_key_orphans": foreign}


def _snapshot(path: Path, artifact: str) -> dict[str, list[tuple[object, ...]]]:
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        return {
            table: [tuple(row) for row in connection.execute(
                f'SELECT {",".join(columns)} FROM "{table}" ORDER BY rowid'
            )]
            for table, columns in PRESERVED_COLUMNS[artifact].items()
        }


def _copy_file(source: Path, destination: Path) -> None:
    if destination.exists():
        raise StagingMigrationError(f"staging artifact already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".copy", dir=destination.parent)
    os.close(descriptor); temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        with temporary.open("rb") as handle: os.fsync(handle.fileno())
        os.replace(temporary, destination)
        directory = os.open(destination.parent, os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _ensure_exact_copy(source: Path, destination: Path, *, reconstruct: bool = False) -> None:
    if destination.exists():
        if not destination.is_file() or _sha256(destination.read_bytes()) != _sha256(source.read_bytes()):
            if not reconstruct: raise StagingMigrationError(f"existing release copy does not match source: {destination}")
            destination.unlink()
        else:
            return
    _copy_file(source, destination)


def _fsync_tree(root: Path) -> None:
    for item in sorted(root.rglob("*"), reverse=True):
        descriptor = os.open(item, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    descriptor = os.open(root, os.O_RDONLY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def _ensure_tree_copy(source: Path, destination: Path, *, reconstruct: bool = False) -> None:
    expected = _blob_evidence(source)
    if destination.exists():
        if destination.is_dir() and _blob_evidence(destination) == expected:
            return
        if not reconstruct: raise StagingMigrationError(f"existing release copy does not match source: {destination}")
        if destination.is_dir(): shutil.rmtree(destination)
        else: destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", suffix=".copy", dir=destination.parent))
    shutil.rmtree(temporary)
    try:
        shutil.copytree(source, temporary); _fsync_tree(temporary)
        os.replace(temporary, destination)
        descriptor = os.open(destination.parent, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)
    finally:
        if temporary.exists(): shutil.rmtree(temporary)


def _assert_quiescent_database(path: Path) -> None:
    sidecars = [Path(f"{path}{suffix}") for suffix in ("-wal", "-journal")]
    active = [item for item in sidecars if item.exists() and item.stat().st_size]
    if active:
        raise StagingMigrationError(
            f"database is not quiescent; active SQLite sidecar: {active[0]}"
        )


def _file_evidence(path: Path) -> dict[str, object]:
    data = path.read_bytes(); return {"bytes": len(data), "sha256": _sha256(data)}


def _blob_evidence(path: Path) -> dict[str, object]:
    if not path.is_dir():
        raise StagingMigrationError(f"blob tree is missing: {path}")
    files = []
    for item in sorted(p for p in path.rglob("*") if p.is_file()):
        data = item.read_bytes(); files.append({"path": item.relative_to(path).as_posix(), "bytes": len(data), "sha256": _sha256(data)})
    return {"files": files, "manifest_sha256": _sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())}


def _set_artifact(manifest: dict[str, object], name: str, state: str, after: object) -> None:
    artifacts = manifest["artifacts"]; assert isinstance(artifacts, dict)
    artifact = artifacts[name]; assert isinstance(artifact, dict)
    artifact["after"] = after; artifact["state"] = state


def _evidence_for(name: str, path: Path) -> dict[str, object]:
    if name in {"identity", *DOMAIN_ORDER}: return database_evidence(path)
    if name == "publication_types": return _file_evidence(path)
    return _blob_evidence(path)


def _bundle_evidence(manifest: Mapping[str, object], root: Path) -> dict[str, object]:
    artifacts = manifest["artifacts"]; assert isinstance(artifacts, dict)
    release_root = root / ".omx" / "releases" / str(manifest["release_id"])
    result: dict[str, object] = {}
    for name in ("identity", *DOMAIN_ORDER, *COPY_ORDER):
        artifact = artifacts[name]; assert isinstance(artifact, dict)
        source_path = _resolve(str(artifact["target_path"] if name == "identity" else artifact["source_path"]), root)
        staging_path = _resolve(str(artifact["staging_path"]), root)
        backup_path = release_root / "backups" / name
        source = _evidence_for(name, source_path); staging = _evidence_for(name, staging_path); backup = _evidence_for(name, backup_path)
        expected_source = artifact["after"] if name == "identity" else artifact["before"]
        if source != expected_source or backup != expected_source or staging != artifact["after"]:
            raise StagingMigrationError(f"artifact {name} bundle evidence mismatch")
        result[name] = {"source": source, "staging": staging, "backup": backup}
    public_ids_path = release_root / "public-id-map.json"
    if not public_ids_path.is_file():
        raise StagingMigrationError("public ID map is missing")
    result["public_id_map"] = _file_evidence(public_ids_path)
    return result


def _verify_public_id_map(manifest: Mapping[str, object], root: Path) -> dict[str, object]:
    artifacts = manifest["artifacts"]; assert isinstance(artifacts, dict)
    path = root / ".omx" / "releases" / str(manifest["release_id"]) / "public-id-map.json"
    if not path.is_file():
        raise StagingMigrationError("public ID map is missing")
    try:
        mapping = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StagingMigrationError("public ID map is invalid") from error
    publications = mapping.get("publications")
    assets = mapping.get("assets")
    if mapping.get("release_id") != manifest["release_id"] or not isinstance(publications, dict) or not isinstance(assets, dict):
        raise StagingMigrationError("public ID map is invalid")
    values = [*publications.values(), *assets.values()]
    try:
        if any(not isinstance(value, str) or str(UUID(value)) != value for value in values):
            raise ValueError
    except ValueError as error:
        raise StagingMigrationError("public ID map contains an invalid UUID") from error
    if len(values) != len(set(values)):
        raise StagingMigrationError("public ID map contains duplicate UUIDs")
    publishing_path = _resolve(str(artifacts["publishing"]["staging_path"]), root)
    assets_path = _resolve(str(artifacts["assets"]["staging_path"]), root)
    with sqlite3.connect(f"file:{publishing_path}?mode=ro", uri=True) as connection:
        actual_publications = {str(row[0]): str(row[1]) for row in connection.execute("SELECT id,public_id FROM publications")}
        projected = {str(row[0]) for row in connection.execute("SELECT asset_public_id FROM publication_public_assets")}
    with sqlite3.connect(f"file:{assets_path}?mode=ro", uri=True) as connection:
        actual_assets = {str(row[0]): str(row[1]) for row in connection.execute("SELECT id,public_id FROM assets")}
    if actual_publications != publications or actual_assets != assets or not projected.issubset(set(assets.values())):
        raise StagingMigrationError("public ID map does not match staged databases")
    return mapping


def _cross_db_audits(manifest: Mapping[str, object], root: Path) -> dict[str, object]:
    artifacts = manifest["artifacts"]; bootstrap = manifest["bootstrap"]
    assert isinstance(artifacts, dict) and isinstance(bootstrap, dict)
    paths = {name: _resolve(str(artifacts[name]["staging_path"]), root) for name in DOMAIN_ORDER}
    identity_path = _resolve(str(artifacts["identity"]["staging_path"]), root)
    workspace_id = str(bootstrap["workspace_id"]); actor_id = str(bootstrap["user_id"])
    workspace_orphans = actor_orphans = 0
    with sqlite3.connect(f"file:{identity_path}?mode=ro", uri=True) as identity_db:
        user_active = identity_db.execute("SELECT 1 FROM users WHERE id=? AND status='ACTIVE'", (actor_id,)).fetchone() is not None
        workspace_active = identity_db.execute("SELECT 1 FROM workspaces WHERE id=? AND status='ACTIVE'", (workspace_id,)).fetchone() is not None
        membership_active = identity_db.execute(
            "SELECT 1 FROM workspace_members WHERE id=? AND workspace_id=? AND user_id=? AND status='ACTIVE'",
            (bootstrap["membership_id"], workspace_id, actor_id),
        ).fetchone() is not None
    if not user_active or not membership_active: actor_orphans += 1
    if not workspace_active or not membership_active: workspace_orphans += 1
    checks = {
        "calendar": (("tasks", ("workspace_id",), ("created_by", "updated_by")), ("topics", ("workspace_id",), ()), ("task_topics", ("workspace_id",), ())),
        "notes": (("notes", ("workspace_id",), ("created_by", "updated_by")), ("note_links", ("workspace_id",), ())),
        "publishing": (("publications", ("workspace_id",), ("created_by",)), ("publication_route_aliases", ("workspace_id",), ()), ("publication_public_assets", ("workspace_id",), ())),
        "assets": (("assets", ("workspace_id",), ("uploaded_by",)), ("asset_references", ("workspace_id",), ())),
    }
    for domain, tables in checks.items():
        with sqlite3.connect(f"file:{paths[domain]}?mode=ro", uri=True) as connection:
            for table, workspace_fields, actor_fields in tables:
                for field in workspace_fields:
                    workspace_orphans += int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {field}!=?", (workspace_id,)).fetchone()[0])
                for field in actor_fields:
                    actor_orphans += int(connection.execute(f"SELECT COUNT(*) FROM {table} WHERE {field}!=?", (actor_id,)).fetchone()[0])
    with sqlite3.connect(f"file:{paths['notes']}?mode=ro", uri=True) as notes_db:
        note_ids = {int(row[0]) for row in notes_db.execute("SELECT id FROM notes")}
        source_links = [
            (str(row[0]), str(row[1]), str(row[2]))
            for row in notes_db.execute("SELECT source_app,source_type,source_id FROM note_links")
        ]
    with sqlite3.connect(f"file:{paths['calendar']}?mode=ro", uri=True) as calendar_db:
        calendar_ids = {str(row[0]) for row in calendar_db.execute("SELECT id FROM tasks")}
    with sqlite3.connect(f"file:{paths['publishing']}?mode=ro", uri=True) as publishing_db:
        missing_publication_notes = [
            str(row[0]) for row in publishing_db.execute("SELECT note_id FROM publications")
            if int(row[0]) not in note_ids
        ]
        projected_assets = {str(row[0]) for row in publishing_db.execute("SELECT asset_public_id FROM publication_public_assets")}
        publication_version_ids = {str(row[0]) for row in publishing_db.execute("SELECT id FROM publication_versions")}
    with sqlite3.connect(f"file:{paths['assets']}?mode=ro", uri=True) as assets_db:
        asset_ids = {str(row[0]) for row in assets_db.execute("SELECT public_id FROM assets")}
        owner_references = [
            (str(row[0]), str(row[1]), str(row[2]))
            for row in assets_db.execute("SELECT owner_type,owner_id,field FROM asset_references")
        ]
    snapshot_counts: dict[tuple[str, str], int] = {}
    valid_source_types = {("calendar", "event")}
    for source_app, source_type, source_id in source_links:
        resource_type = "calendar_source" if (source_app, source_type) == ("calendar", "event") else None
        existing = calendar_ids
        if resource_type is not None and source_id not in existing:
            key = (resource_type, source_id); snapshot_counts[key] = snapshot_counts.get(key, 0) + 1
    for source_id in missing_publication_notes:
        key = ("note_source", source_id)
        snapshot_counts[key] = snapshot_counts.get(key, 0) + 1
    owner_orphans = 0
    for owner_type, owner_id, field in owner_references:
        if (owner_type, field) == ("note", "content"): valid = owner_id in {str(value) for value in note_ids}
        elif (owner_type, field) == ("publication_version", "markdown"): valid = owner_id in publication_version_ids
        elif owner_type == "upload_session":
            # Upload-session references are temporary and must not survive a
            # coordinated staging snapshot as active resource ownership.
            valid = False
        else: valid = False
        owner_orphans += int(not valid)
    owner_orphans += sum(
        1 for source_app, source_type, _ in source_links
        if (source_app, source_type) not in valid_source_types
    )
    configured = manifest["audits"]; assert isinstance(configured, dict)
    expected_orphans = configured["allowed_snapshot_orphans"]; assert isinstance(expected_orphans, list)
    reasons = {(str(item["resource_type"]), str(item["resource_id"])): str(item["reason"]) for item in expected_orphans}
    configured_keys = set(reasons)
    publication_note_orphans = sum(
        1 for source_id in missing_publication_notes
        if ("note_source", source_id) not in configured_keys
    )
    allowed = [
        {"resource_type": key[0], "resource_id": key[1], "count": count, "reason": reasons.get(key, "unapproved missing source")}
        for key, count in sorted(snapshot_counts.items())
    ]
    return {
        "actor_orphans": actor_orphans,
        "workspace_orphans": workspace_orphans,
        "publication_note_orphans": publication_note_orphans,
        "asset_reference_orphans": len(projected_assets - asset_ids) + owner_orphans,
        "allowed_snapshot_orphans": allowed,
    }


def stage_domains(manifest_path: Path, *, workspace_root: Path = WORKSPACE_ROOT) -> dict[str, object]:
    manifest_path = Path(manifest_path).resolve(); root = Path(workspace_root).resolve()
    manifest = read_manifest(manifest_path)
    bootstrap = manifest["bootstrap"]; artifacts = manifest["artifacts"]
    assert isinstance(bootstrap, dict) and isinstance(artifacts, dict)
    context = {"workspace_id": bootstrap["workspace_id"], "actor_id": bootstrap["user_id"], "release_id": manifest["release_id"]}
    release_root = root / ".omx" / "releases" / str(manifest["release_id"])
    backup_root = release_root / "backups"
    source_hashes: dict[str, str] = {}
    preservation: dict[str, object] = {}

    # Path/type/quiescence checks happen before the first SQLite open. URI
    # mode=ro is then used for every operational database read.
    source_metadata: dict[str, tuple[Path, dict[str, object]]] = {}
    for name in DOMAIN_ORDER:
        artifact = artifacts[name]; assert isinstance(artifact, dict)
        source = _resolve(str(artifact["source_path"]), root)
        if not source.is_file(): raise StagingMigrationError(f"database is missing: {source}")
        _assert_quiescent_database(source)
        source_metadata[name] = (source, {})
    identity = artifacts["identity"]; assert isinstance(identity, dict)
    identity_target = _resolve(str(identity["target_path"]), root)
    if not identity_target.is_file(): raise StagingMigrationError(f"database is missing: {identity_target}")
    _assert_quiescent_database(identity_target)
    for name in COPY_ORDER:
        artifact = artifacts[name]; assert isinstance(artifact, dict)
        source = _resolve(str(artifact["source_path"]), root)
        if name == "publication_types" and not source.is_file(): raise StagingMigrationError(f"file is missing: {source}")
        if name == "asset_blobs" and not source.is_dir(): raise StagingMigrationError(f"blob tree is missing: {source}")
    for name, (source, _) in tuple(source_metadata.items()):
        evidence = database_evidence(source); artifact = artifacts[name]; assert isinstance(artifact, dict)
        expected = artifact["before"]
        if not isinstance(expected, dict) or evidence["file_sha256"] != expected["file_sha256"]:
            raise StagingMigrationError(f"artifact {name} source checksum drifted")
        source_metadata[name] = (source, evidence); source_hashes[name] = str(evidence["file_sha256"])
    if identity["state"] != "verified" or not isinstance(identity["after"], dict) or database_evidence(identity_target) != identity["after"]:
        raise StagingMigrationError("identity target no longer matches bootstrap evidence")
    for name in COPY_ORDER:
        artifact = artifacts[name]; assert isinstance(artifact, dict)
        source = _resolve(str(artifact["source_path"]), root)
        evidence = _file_evidence(source) if name == "publication_types" else _blob_evidence(source)
        if evidence != artifact["before"]: raise StagingMigrationError(f"artifact {name} source checksum drifted")

    # Resolve the immutable public-asset projection outside every migration
    # connection; callbacks still own exactly one staging database boundary.
    publishing_source = _resolve(str(artifacts["publishing"]["source_path"]), root)
    assets_source = _resolve(str(artifacts["assets"]["source_path"]), root)
    with sqlite3.connect(f"file:{publishing_source}?mode=ro", uri=True) as publications_db:
        versions = {str(row[0]): (int(row[1]), int(row[2])) for row in publications_db.execute("SELECT id,publication_id,version FROM publication_versions")}
        publication_ids = [str(row[0]) for row in publications_db.execute("SELECT id FROM publications ORDER BY id")]
    projection = []
    with sqlite3.connect(f"file:{assets_source}?mode=ro", uri=True) as assets_db:
        asset_ids = [str(row[0]) for row in assets_db.execute("SELECT id FROM assets ORDER BY id")]
    public_ids_path = release_root / "public-id-map.json"
    if public_ids_path.exists():
        public_ids = json.loads(public_ids_path.read_text(encoding="utf-8"))
    else:
        if any(
            artifacts[name]["state"] != "pending"
            or _resolve(str(artifacts[name]["staging_path"]), root).exists()
            for name in DOMAIN_ORDER
        ):
            raise StagingMigrationError("public ID map is missing for an in-progress release")
        public_ids = {
            "release_id": manifest["release_id"],
            "publications": {item: str(uuid4()) for item in publication_ids},
            "assets": {item: str(uuid4()) for item in asset_ids},
        }
        _atomic_json(public_ids_path, public_ids)
    if public_ids.get("release_id") != manifest["release_id"] or set(public_ids.get("publications", {})) != set(publication_ids) or set(public_ids.get("assets", {})) != set(asset_ids):
        raise StagingMigrationError("public ID map does not match the release source inventory")
    for value in (*public_ids["publications"].values(), *public_ids["assets"].values()):
        if not isinstance(value, str) or str(UUID(value)) != value: raise StagingMigrationError("public ID map contains an invalid UUID")
    if len([*public_ids["publications"].values(), *public_ids["assets"].values()]) != len(set([*public_ids["publications"].values(), *public_ids["assets"].values()])):
        raise StagingMigrationError("public ID map contains duplicate UUIDs")
    context["publication_public_ids"] = public_ids["publications"]
    context["asset_public_ids"] = public_ids["assets"]
    with sqlite3.connect(f"file:{assets_source}?mode=ro", uri=True) as assets_db:
        for asset_id, owner_id in assets_db.execute("SELECT asset_id,owner_id FROM asset_references WHERE owner_type='publication_version' AND field='markdown' ORDER BY id"):
            version = versions.get(str(owner_id))
            if version is not None:
                projection.append((*version, public_ids["assets"][str(asset_id)]))
    context["public_asset_projection"] = tuple(sorted(set(projection)))

    identity_staging = _resolve(str(identity["staging_path"]), root)
    _ensure_exact_copy(identity_target, backup_root / "identity")
    _ensure_exact_copy(identity_target, identity_staging)

    for name in DOMAIN_ORDER:
        artifact = artifacts[name]; assert isinstance(artifact, dict)
        if artifact["state"] not in {"pending", "applied", "verified"}:
            raise StagingMigrationError(f"artifact {name} cannot be staged from {artifact['state']}")
        source, before = source_metadata[name]
        staging = _resolve(str(artifact["staging_path"]), root)
        target = _resolve(str(artifact["target_path"]), root)
        if staging in {source, target}:
            raise StagingMigrationError(f"artifact {name} staging path is operational")
        source_snapshot = _snapshot(source, name)
        backup = backup_root / name
        pending = artifact["state"] == "pending"
        _ensure_exact_copy(source, backup, reconstruct=pending)
        if staging.exists() and pending:
            try:
                staged_preflight = database_evidence(staging)
                if staged_preflight["quick_check"] != "ok" or staged_preflight["user_version"] not in {artifact["from_version"], artifact["to_version"]}:
                    raise StagingMigrationError("incomplete staging database")
            except (sqlite3.DatabaseError, StagingMigrationError, OSError):
                staging.unlink(missing_ok=True); _copy_file(source, staging)
        elif not staging.exists(): _copy_file(source, staging)
        if _sha256(backup.read_bytes()) != source_hashes[name]:
            raise StagingMigrationError(f"artifact {name} backup is not byte-identical")
        with sqlite3.connect(staging) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            target_version, steps = MIGRATIONS[name]
            migrate(connection, target_version=target_version, migrations=steps, context=context)
        after = database_evidence(staging)
        if after["quick_check"] != "ok" or after["foreign_key_orphans"] != 0:
            raise StagingMigrationError(f"artifact {name} staging verification failed")
        staged_snapshot = _snapshot(staging, name)
        if staged_snapshot != source_snapshot:
            raise StagingMigrationError(f"artifact {name} visible content was not preserved")
        preservation[name] = {table: len(rows) for table, rows in source_snapshot.items()}
        if artifact["state"] == "verified":
            if after != artifact["after"]: raise StagingMigrationError(f"artifact {name} verified evidence changed")
        else:
            if artifact["state"] == "applied":
                if after != artifact["after"]: raise StagingMigrationError(f"artifact {name} applied evidence changed")
            else:
                updated = deepcopy(manifest); _set_artifact(updated, name, "applied", after); write_manifest(manifest_path, updated); manifest = updated
            updated = deepcopy(manifest); _set_artifact(updated, name, "verified", after); write_manifest(manifest_path, updated); manifest = updated

    artifacts = manifest["artifacts"]; assert isinstance(artifacts, dict)
    for name in COPY_ORDER:
        artifact = artifacts[name]; assert isinstance(artifact, dict)
        source = _resolve(str(artifact["source_path"]), root); staging = _resolve(str(artifact["staging_path"]), root)
        backup = backup_root / name
        if artifact["state"] not in {"pending", "applied", "verified"}: raise StagingMigrationError(f"artifact {name} cannot be staged")
        pending = artifact["state"] == "pending"
        if name == "publication_types":
            _ensure_exact_copy(source, backup, reconstruct=pending); _ensure_exact_copy(source, staging, reconstruct=pending); after = _file_evidence(staging)
        else:
            _ensure_tree_copy(source, backup, reconstruct=pending)
            _ensure_tree_copy(source, staging, reconstruct=pending)
            after = _blob_evidence(staging)
        if after != artifact["before"]:
            raise StagingMigrationError(f"artifact {name} was not byte-preserved")
        if artifact["state"] == "verified":
            if after != artifact["after"]: raise StagingMigrationError(f"artifact {name} verified evidence changed")
        else:
            if artifact["state"] == "applied":
                if after != artifact["after"]: raise StagingMigrationError(f"artifact {name} applied evidence changed")
            else:
                updated = deepcopy(manifest); _set_artifact(updated, name, "applied", after); write_manifest(manifest_path, updated); manifest = updated
            updated = deepcopy(manifest); _set_artifact(updated, name, "verified", after); write_manifest(manifest_path, updated); manifest = updated

    for name, expected_hash in source_hashes.items():
        artifact = artifacts[name]; assert isinstance(artifact, dict)
        if _sha256(_resolve(str(artifact["source_path"]), root).read_bytes()) != expected_hash:
            raise StagingMigrationError(f"artifact {name} source changed during staging")
    audits = _cross_db_audits(manifest, root)
    _verify_public_id_map(manifest, root)
    expected_audits = manifest["audits"]; assert isinstance(expected_audits, dict)
    integer_audits = ("actor_orphans", "workspace_orphans", "publication_note_orphans", "asset_reference_orphans")
    if any(audits[key] != int(expected_audits[key]) for key in integer_audits) or audits["allowed_snapshot_orphans"] != expected_audits["allowed_snapshot_orphans"]:
        raise StagingMigrationError(f"cross-database audit failed: {audits}")
    _atomic_json(release_root / "cross-db-audit.json", {"release_id": manifest["release_id"], **audits})
    _atomic_json(release_root / "preservation-report.json", {"release_id": manifest["release_id"], "preserved_rows": preservation, "source_sha256": source_hashes})
    return manifest


def verify_staging(manifest_path: Path, *, workspace_root: Path = WORKSPACE_ROOT) -> dict[str, object]:
    root = Path(workspace_root).resolve(); manifest = read_manifest(Path(manifest_path).resolve())
    bootstrap = manifest["bootstrap"]; artifacts = manifest["artifacts"]
    assert isinstance(bootstrap, dict) and isinstance(artifacts, dict)
    context = {"workspace_id": bootstrap["workspace_id"], "actor_id": bootstrap["user_id"], "release_id": manifest["release_id"]}
    for name in DOMAIN_ORDER:
        artifact = artifacts[name]; assert isinstance(artifact, dict)
        if artifact["state"] != "verified": raise StagingMigrationError(f"artifact {name} is not verified")
        source = _resolve(str(artifact["source_path"]), root); staging = _resolve(str(artifact["staging_path"]), root)
        if _snapshot(source, name) != _snapshot(staging, name): raise StagingMigrationError(f"artifact {name} preservation check failed")
        with sqlite3.connect(staging) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            target, steps = MIGRATIONS[name]; result = migrate(connection, target_version=target, migrations=steps, context=context)
            if result.changed: raise StagingMigrationError(f"artifact {name} verification unexpectedly migrated")
        if database_evidence(staging) != artifact["after"]: raise StagingMigrationError(f"artifact {name} evidence changed")
        if _sha256(source.read_bytes()) != artifact["before"]["file_sha256"]: raise StagingMigrationError(f"artifact {name} source checksum drifted")
    for name in COPY_ORDER:
        artifact = artifacts[name]; assert isinstance(artifact, dict)
        staging = _resolve(str(artifact["staging_path"]), root)
        evidence = _file_evidence(staging) if name == "publication_types" else _blob_evidence(staging)
        if artifact["state"] != "verified" or evidence != artifact["after"] or evidence != artifact["before"]:
            raise StagingMigrationError(f"artifact {name} verification failed")
    audits = _cross_db_audits(manifest, root); expected = manifest["audits"]; assert isinstance(expected, dict)
    integer_audits = ("actor_orphans", "workspace_orphans", "publication_note_orphans", "asset_reference_orphans")
    if any(audits[key] != int(expected[key]) for key in integer_audits) or audits["allowed_snapshot_orphans"] != expected["allowed_snapshot_orphans"]: raise StagingMigrationError(f"cross-database audit failed: {audits}")
    _verify_public_id_map(manifest, root)
    _bundle_evidence(manifest, root)
    return manifest


def prepare_cutover(manifest_path: Path, *, workspace_root: Path = WORKSPACE_ROOT) -> tuple[Path, Path]:
    root = Path(workspace_root).resolve(); manifest = verify_staging(manifest_path, workspace_root=root)
    release_root = root / ".omx" / "releases" / str(manifest["release_id"])
    artifacts = manifest["artifacts"]; assert isinstance(artifacts, dict)
    bundle_evidence = _bundle_evidence(manifest, root)
    names = ("identity", "calendar", "notes", "publishing", "assets", "publication_types", "asset_blobs")
    rollback = {"manifest_version": 1, "release_id": manifest["release_id"], "all_or_nothing": True, "production_promotion_allowed": False, "artifacts": []}
    cutover = {"manifest_version": 1, "release_id": manifest["release_id"], "mode": "staging_only", "production_promotion_allowed": False, "requires_phase6_compatible_binary": True, "ordered_artifacts": [], "bundle_evidence": bundle_evidence}
    for name in names:
        artifact = artifacts[name]; assert isinstance(artifact, dict)
        backup = release_root / "backups" / name
        cutover["ordered_artifacts"].append({"name": name, "staging_path": artifact["staging_path"], "target_path": artifact["target_path"]})
        rollback["artifacts"].append({"name": name, "backup_path": str(backup.relative_to(root)), "target_path": artifact["target_path"], "before": bundle_evidence[name]["source"], "verified_backup": bundle_evidence[name]["backup"]})
    cutover_path = release_root / "coordinated-cutover-plan.json"; rollback_path = release_root / "rollback-manifest.json"
    _atomic_json(cutover_path, cutover); _atomic_json(rollback_path, rollback)
    return cutover_path, rollback_path
