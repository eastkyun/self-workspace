"""Secure, recoverable bootstrap of the explicit legacy data owner."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from argon2 import PasswordHasher

from identity.database import (
    IDENTITY_SCHEMA_VERSION,
    database_connection,
    initialize_database,
    resolve_database_path,
)
from identity.foundation import (
    BUILTIN_ROLES,
    FOUNDATION_APPS,
    foundation_app_checksum,
    foundation_catalog_checksum,
    permission_code,
    permission_id,
    role_permission_codes,
)
from identity.passwords import PRODUCTION_PASSWORD_HASHER, hash_password, verify_password
from identity.primitives import new_uuid, normalize_email
from shared.migrations import ManifestError, read_manifest, write_manifest


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = WORKSPACE_ROOT / "instance" / "migration-manifest.json"
CATALOG_PATH = WORKSPACE_ROOT / "shared" / "apps.json"
TABLES = (
    "users", "workspaces", "apps", "roles", "workspace_members", "sessions",
    "workspace_invitations", "workspace_apps", "permissions", "role_permissions",
    "member_permission_overrides", "audit_logs",
    "password_reset_tokens",
)


class BootstrapError(RuntimeError):
    """Bootstrap state is partial, mismatched, or cannot be made durable."""


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    user_id: str
    workspace_id: str
    membership_id: str
    release_id: str
    created: bool


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _display_name(email: str) -> str:
    local = email.partition("@")[0].strip()
    return local or "Owner"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _git_value(label: str, *args: str) -> str:
    try:
        result = subprocess.run(
            ("git", *args), cwd=WORKSPACE_ROOT, check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BootstrapError(f"cannot collect Git {label} evidence") from error
    value = result.stdout.strip()
    if not value:
        raise BootstrapError(f"Git {label} evidence is empty")
    return value


def _row_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {table: int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]) for table in TABLES}


def _database_evidence(path: Path) -> dict[str, object]:
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        schema_rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        schema_payload = json.dumps([tuple(row) for row in schema_rows], separators=(",", ":"), ensure_ascii=False).encode()
        content: list[object] = []
        for table in TABLES:
            rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            content.append((table, [tuple(row) for row in rows]))
        content_payload = json.dumps(content, separators=(",", ":"), ensure_ascii=False).encode()
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        counts = _row_counts(connection)
    data = path.read_bytes()
    return {
        "bytes": len(data),
        "file_sha256": _sha256(data),
        "schema_sha256": _sha256(schema_payload),
        "content_sha256": _sha256(content_payload),
        "user_version": version,
        "rows": counts,
        "quick_check": quick,
        "foreign_key_orphans": len(foreign_keys),
    }


def _legacy_database_evidence(path: Path) -> dict[str, object]:
    """Capture deterministic evidence without assuming a domain schema."""
    if not path.is_file():
        raise BootstrapError(f"legacy database is missing: {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        schema_rows = connection.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        schema_payload = json.dumps([tuple(row) for row in schema_rows], separators=(",", ":"), ensure_ascii=False).encode()
        content: list[object] = []
        counts: dict[str, int] = {}
        for table in tables:
            rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall()
            counts[table] = len(rows)
            content.append((table, [tuple(row) for row in rows]))
        content_payload = json.dumps(content, separators=(",", ":"), ensure_ascii=False, default=str).encode()
        quick = connection.execute("PRAGMA quick_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    data = path.read_bytes()
    return {"bytes": len(data), "file_sha256": _sha256(data), "schema_sha256": _sha256(schema_payload), "content_sha256": _sha256(content_payload), "user_version": version, "rows": counts, "quick_check": quick, "foreign_key_orphans": len(foreign_keys)}


def _file_evidence(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise BootstrapError(f"legacy file is missing: {path}")
    data = path.read_bytes()
    return {"bytes": len(data), "sha256": _sha256(data)}


def _blob_evidence(path: Path) -> dict[str, object]:
    if not path.is_dir():
        raise BootstrapError(f"legacy blob directory is missing: {path}")
    files = []
    for item in sorted(candidate for candidate in path.rglob("*") if candidate.is_file()):
        data = item.read_bytes()
        files.append({"path": item.relative_to(path).as_posix(), "bytes": len(data), "sha256": _sha256(data)})
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {"files": files, "manifest_sha256": _sha256(payload)}


def _artifact(release_id: str, name: str, source: str | None, target: str, from_version: int, to_version: int, *, before: object = None, after: object = None, state: str = "pending") -> dict[str, object]:
    return {
        "source_path": source,
        "staging_path": f".omx/releases/{release_id}/staging/{name}",
        "target_path": target,
        "from_version": from_version,
        "to_version": to_version,
        "release_id": release_id,
        "state": state,
        "before": before,
        "after": after,
    }


def _source_evidence() -> dict[str, object]:
    if os.environ.get("WORKSPACE_SINGLE_WORKER_ASSERTED", "").strip().casefold() != "true":
        raise BootstrapError(
            "WORKSPACE_SINGLE_WORKER_ASSERTED=true is required for bootstrap"
        )
    # An empty porcelain result is valid evidence, unlike an empty commit or branch.
    try:
        status_result = subprocess.run(
            ("git", "status", "--porcelain=v1"), cwd=WORKSPACE_ROOT,
            check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise BootstrapError("cannot collect Git worktree evidence") from error
    status = status_result.stdout.strip()
    if status:
        raise BootstrapError("bootstrap requires a clean Git worktree")
    return {
        "git_commit": _git_value("commit", "rev-parse", "HEAD"),
        "git_branch": _git_value("branch", "branch", "--show-current"),
        "worktree_porcelain_sha256": _sha256(status.encode()),
        "application_version": "workspace-rbac-v1",
        "single_worker_asserted": True,
    }


def _new_manifest(
    result: BootstrapResult,
    email: str,
    database_path: Path,
    source_evidence: dict[str, object],
) -> dict[str, object]:
    release_id = result.release_id
    identity_target = str(database_path.relative_to(WORKSPACE_ROOT)) if database_path.is_relative_to(WORKSPACE_ROOT) else str(database_path)
    artifacts = {
        "identity": _artifact(release_id, "identity", None, identity_target, 0, IDENTITY_SCHEMA_VERSION, after=_database_evidence(database_path), state="verified"),
        "calendar": _artifact(release_id, "calendar", "instance/calendar/tasks.db", "instance/calendar/tasks.db", 2, 3, before=_legacy_database_evidence(WORKSPACE_ROOT / "instance/calendar/tasks.db")),
        "notes": _artifact(release_id, "notes", "instance/notes/notes.db", "instance/notes/notes.db", 1, 2, before=_legacy_database_evidence(WORKSPACE_ROOT / "instance/notes/notes.db")),
        "publishing": _artifact(release_id, "publishing", "instance/publishing/publishing.db", "instance/publishing/publishing.db", 1, 2, before=_legacy_database_evidence(WORKSPACE_ROOT / "instance/publishing/publishing.db")),
        "assets": _artifact(release_id, "assets", "instance/assets/assets.db", "instance/assets/assets.db", 1, 2, before=_legacy_database_evidence(WORKSPACE_ROOT / "instance/assets/assets.db")),
        "publication_types": _artifact(release_id, "publication_types", "instance/publishing/types.json", "instance/publishing/types.json", 1, 1, before=_file_evidence(WORKSPACE_ROOT / "instance/publishing/types.json")),
        "asset_blobs": _artifact(release_id, "asset_blobs", "instance/assets/blobs", "instance/assets/blobs", 1, 1, before=_blob_evidence(WORKSPACE_ROOT / "instance/assets/blobs")),
    }
    return {
        "manifest_version": 1,
        "release_id": release_id,
        "created_at": _timestamp(),
        "source": source_evidence,
        "bootstrap": {
            "email_normalized_sha256": _sha256(email.encode()),
            "user_id": result.user_id,
            "workspace_id": result.workspace_id,
            "membership_id": result.membership_id,
        },
        "catalog": {
            # The fixed migration contract records the immutable legacy source
            # bytes here.  checksum_after is the normalized Phase 1 projection
            # seeded into Identity, not a second manifest artifact.
            "path": "shared/apps.json",
            "sha256_before": _sha256(CATALOG_PATH.read_bytes()),
            "manifest_checksum_after": foundation_catalog_checksum(),
        },
        "artifacts": artifacts,
        "audits": {"actor_orphans": 0, "workspace_orphans": 0, "publication_note_orphans": 0, "asset_reference_orphans": 0, "allowed_snapshot_orphans": []},
        "compatibility": {"canonical_smoke": "pending", "legacy_api_smoke": "pending", "public_alias_smoke": "pending"},
        "cutover": {"maintenance_lock": False, "write_fence": False, "bundle_verified": False, "state": "pending", "steps": []},
    }


def _expected_counts() -> dict[str, int]:
    permissions = sum(len(app.permissions) for app in FOUNDATION_APPS)
    bindings = sum(len(role_permission_codes(role[0])) for role in BUILTIN_ROLES)
    return {
        "users": 1, "workspaces": 1, "apps": len(FOUNDATION_APPS), "roles": 4,
        "workspace_members": 1, "sessions": 0, "workspace_invitations": 0,
        "workspace_apps": len(FOUNDATION_APPS), "permissions": permissions,
        "role_permissions": bindings, "member_permission_overrides": 0, "audit_logs": 1,
        "password_reset_tokens": 0,
    }


def _preflight_manifest_identity_database(path: Path) -> None:
    """Reject manifest/DB loss or rollback before initialization can mutate it."""

    if not path.is_file():
        raise BootstrapError("migration manifest exists but identity database is missing")
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise BootstrapError("migration manifest identity database cannot be read") from error
    if version != IDENTITY_SCHEMA_VERSION:
        raise BootstrapError("migration manifest identity database version does not match")


def _validate_existing(connection: sqlite3.Connection, email: str, password: str, hasher: PasswordHasher) -> BootstrapResult:
    counts = _row_counts(connection)
    if counts != _expected_counts():
        raise BootstrapError("identity database contains partial or non-bootstrap state")
    user = connection.execute("SELECT * FROM users WHERE email_normalized=?", (email,)).fetchone()
    if (
        user is None
        or user["status"] != "ACTIVE"
        or user["display_name"] != _display_name(email)
        or user["disabled_at"] is not None
        or not verify_password(user["password_hash"], password, hasher=hasher)
    ):
        raise BootstrapError("existing bootstrap identity does not exactly match")
    workspace = connection.execute("SELECT * FROM workspaces").fetchone()
    membership = connection.execute("SELECT * FROM workspace_members").fetchone()
    if (
        workspace["type"] != "PERSONAL"
        or workspace["status"] != "ACTIVE"
        or workspace["created_by"] != user["id"]
        or workspace["name"] != f"{user['display_name']}'s Workspace"
        or workspace["description"] != ""
        or workspace["logo_asset_id"] is not None
        or workspace["timezone"] != "Asia/Seoul"
        or workspace["deleted_at"] is not None
        or membership["workspace_id"] != workspace["id"]
        or membership["user_id"] != user["id"]
        or membership["status"] != "ACTIVE"
    ):
        raise BootstrapError("existing bootstrap workspace does not exactly match")
    role = connection.execute("SELECT system_role FROM roles WHERE id=?", (membership["role_id"],)).fetchone()
    if role is None or role[0] != "OWNER":
        raise BootstrapError("existing bootstrap membership is not Owner")

    expected_roles = {(key, name, system) for key, name, system in BUILTIN_ROLES}
    actual_roles = {
        (row["key"], row["name"], row["system_role"])
        for row in connection.execute(
            "SELECT key, name, system_role FROM roles "
            "WHERE workspace_id=? AND builtin=1 AND status='ACTIVE' "
            "AND description='' AND created_by=?",
            (workspace["id"], user["id"]),
        )
    }
    if actual_roles != expected_roles:
        raise BootstrapError("existing built-in roles do not exactly match")

    expected_apps = {
        (
            app.id, app.key, app.name, app.description, app.icon, app.category,
            app.version, foundation_app_checksum(app),
        )
        for app in FOUNDATION_APPS
    }
    actual_apps = {
        tuple(row)
        for row in connection.execute(
            "SELECT id, key, name, description, icon, category, version, manifest_checksum "
            "FROM apps WHERE builtin=1 AND status='ACTIVE'",
        )
    }
    if actual_apps != expected_apps:
        raise BootstrapError("existing foundation app catalog does not exactly match")
    expected_workspace_apps = {
        (app.id, app.display_order, int(app.default_enabled)) for app in FOUNDATION_APPS
    }
    actual_workspace_apps = {
        (row["app_id"], row["display_order"], row["enabled"])
        for row in connection.execute(
            "SELECT app_id, display_order, enabled FROM workspace_apps "
            "WHERE workspace_id=? AND settings_json='{}' AND updated_by=?",
            (workspace["id"], user["id"]),
        )
    }
    if actual_workspace_apps != expected_workspace_apps:
        raise BootstrapError("existing workspace app defaults do not exactly match")

    expected_permissions = {
        (
            permission_id(code), code, app.id,
            int(code in {"workspace.ownership.transfer", "workspace.delete"}),
        )
        for app in FOUNDATION_APPS
        for suffix in app.permissions
        for code in (permission_code(app, suffix),)
    }
    actual_permissions = {
        (row["id"], row["code"], row["app_id"], row["owner_only"])
        for row in connection.execute(
            "SELECT id, code, app_id, owner_only FROM permissions WHERE description=''"
        )
    }
    if actual_permissions != expected_permissions:
        raise BootstrapError("existing permission catalog does not exactly match")
    for role_key, _, _ in BUILTIN_ROLES:
        actual_codes = {
            row[0]
            for row in connection.execute(
                "SELECT permission.code FROM role_permissions binding "
                "JOIN roles role ON role.id=binding.role_id "
                "JOIN permissions permission ON permission.id=binding.permission_id "
                "WHERE role.workspace_id=? AND role.key=? AND binding.effect='ALLOW'",
                (workspace["id"], role_key),
            )
        }
        if actual_codes != role_permission_codes(role_key):
            raise BootstrapError("existing role permission bindings do not exactly match")
    audit = connection.execute(
        "SELECT actor_user_id, action, target_type, target_id, metadata_json "
        "FROM audit_logs WHERE workspace_id=?",
        (workspace["id"],),
    ).fetchone()
    expected_metadata = json.dumps(
        {"membership_id": membership["id"]}, sort_keys=True, separators=(",", ":")
    )
    if audit is None or tuple(audit) != (
        user["id"], "bootstrap.owner.created", "workspace", workspace["id"], expected_metadata
    ):
        raise BootstrapError("existing bootstrap audit does not exactly match")
    return BootstrapResult(user["id"], workspace["id"], membership["id"], "", False)


def _insert_bootstrap(connection: sqlite3.Connection, email: str, password: str, hasher: PasswordHasher, failpoint: Callable[[str], None]) -> BootstrapResult:
    now = _timestamp()
    user_id, workspace_id, membership_id = new_uuid(), new_uuid(), new_uuid()
    display_name = _display_name(email)
    connection.execute("INSERT INTO users VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, NULL)", (user_id, email, display_name, hash_password(password, hasher=hasher), now, now))
    failpoint("user")
    connection.execute("INSERT INTO workspaces VALUES (?, 'PERSONAL', ?, '', NULL, 'Asia/Seoul', 'ACTIVE', ?, ?, ?, NULL)", (workspace_id, f"{display_name}'s Workspace", user_id, now, now))
    failpoint("workspace")
    role_ids: dict[str, str] = {}
    for key, name, system_role in BUILTIN_ROLES:
        role_ids[key] = new_uuid()
        connection.execute("INSERT INTO roles VALUES (?, ?, ?, ?, '', 1, ?, 'ACTIVE', ?, ?, ?)", (role_ids[key], workspace_id, key, name, system_role, user_id, now, now))
    failpoint("roles")
    for app in FOUNDATION_APPS:
        connection.execute("INSERT INTO apps VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'ACTIVE', ?, ?, ?)", (app.id, app.key, app.name, app.description, app.icon, app.category, app.version, foundation_app_checksum(app), now, now))
        connection.execute("INSERT INTO workspace_apps VALUES (?, ?, ?, ?, '{}', ?, ?, ?)", (workspace_id, app.id, int(app.default_enabled), app.display_order, user_id, now, now))
        for permission in app.permissions:
            code = permission_code(app, permission)
            owner_only = int(code in {"workspace.ownership.transfer", "workspace.delete"})
            connection.execute("INSERT INTO permissions VALUES (?, ?, ?, ?, '', ?, ?, ?)", (permission_id(code), code, app.id, code, owner_only, now, now))
    failpoint("catalog")
    connection.execute("INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)", (membership_id, workspace_id, user_id, role_ids["owner"], now, now, now))
    failpoint("membership")
    for role_key, role_id in role_ids.items():
        connection.executemany("INSERT INTO role_permissions VALUES (?, ?, 'ALLOW', ?)", ((role_id, permission_id(code), now) for code in sorted(role_permission_codes(role_key))))
    failpoint("bindings")
    connection.execute("INSERT INTO audit_logs VALUES (?, ?, 'bootstrap.owner.created', 'workspace', ?, ?, ?)", (workspace_id, user_id, workspace_id, json.dumps({"membership_id": membership_id}, sort_keys=True, separators=(",", ":")), now))
    failpoint("audit")
    return BootstrapResult(user_id, workspace_id, membership_id, str(uuid4()), True)


def bootstrap_owner(email: str, password: str, *, database_path: str | Path | None = None, manifest_path: str | Path | None = None, hasher: PasswordHasher = PRODUCTION_PASSWORD_HASHER, _failpoint: Callable[[str], None] | None = None) -> BootstrapResult:
    normalized = normalize_email(email)
    if not password:
        raise BootstrapError("password must not be empty")
    manifest = Path(manifest_path) if manifest_path is not None else DEFAULT_MANIFEST_PATH
    existing_manifest: dict[str, Any] | None = None
    if manifest.exists():
        try:
            existing_manifest = read_manifest(manifest)
        except ManifestError as error:
            raise BootstrapError("migration manifest is invalid") from error
        expected_email_hash = _sha256(normalized.encode())
        if existing_manifest["bootstrap"]["email_normalized_sha256"] != expected_email_hash:
            raise BootstrapError("migration manifest belongs to a different bootstrap identity")
    source_evidence = _source_evidence() if existing_manifest is None else None
    resolved_database = resolve_database_path(database_path)
    if existing_manifest is not None:
        _preflight_manifest_identity_database(resolved_database)
    database = initialize_database(resolved_database)
    failpoint = _failpoint or (lambda _stage: None)
    with database_connection(database) as connection:
        if any(_row_counts(connection).values()):
            result = _validate_existing(connection, normalized, password, hasher)
        else:
            if existing_manifest is not None:
                raise BootstrapError("migration manifest exists but identity database is empty")
            result = _insert_bootstrap(connection, normalized, password, hasher, failpoint)
    failpoint("identity_committed")

    if existing_manifest is not None:
        existing = existing_manifest
        bootstrap = existing["bootstrap"]
        expected_email_hash = _sha256(normalized.encode())
        if bootstrap != {"email_normalized_sha256": expected_email_hash, "user_id": result.user_id, "workspace_id": result.workspace_id, "membership_id": result.membership_id}:
            raise BootstrapError("migration manifest belongs to a different bootstrap identity")
        catalog = existing["catalog"]
        if (
            catalog["path"] != "shared/apps.json"
            or catalog["sha256_before"] != _sha256(CATALOG_PATH.read_bytes())
            or catalog["manifest_checksum_after"] != foundation_catalog_checksum()
        ):
            raise BootstrapError("migration manifest catalog evidence does not match")
        identity_artifact = existing["artifacts"]["identity"]
        if (
            identity_artifact["state"] != "verified"
            or identity_artifact["after"] != _database_evidence(database)
        ):
            raise BootstrapError("migration manifest identity evidence does not match")
        return BootstrapResult(result.user_id, result.workspace_id, result.membership_id, str(existing["release_id"]), False)

    if not result.release_id:
        result = BootstrapResult(result.user_id, result.workspace_id, result.membership_id, str(uuid4()), False)
    assert source_evidence is not None
    write_manifest(manifest, _new_manifest(result, normalized, database, source_evidence))
    return result
