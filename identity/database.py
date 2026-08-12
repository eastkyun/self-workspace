"""Versioned SQLite database for users, workspaces, and authorization state."""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from collections.abc import Iterable, Iterator, Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from shared.migrations import MigrationError, MigrationStep, get_user_version, migrate
from shared.migrations import verify_database as verify_sqlite_database

from .primitives import normalize_email


WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = WORKSPACE_ROOT / "instance" / "identity" / "identity.db"
IDENTITY_SCHEMA_VERSION = 8


class IdentityDatabaseError(RuntimeError):
    """The identity database does not satisfy its versioned contract."""


class _IdentityConnection(sqlite3.Connection):
    """Connection whose public transaction boundary can be owned by a context."""

    _boundary_owned = False

    def commit(self) -> None:
        if self._boundary_owned:
            raise IdentityDatabaseError("identity transaction is owned by database_connection")
        super().commit()

    def rollback(self) -> None:
        if self._boundary_owned:
            raise IdentityDatabaseError("identity transaction is owned by database_connection")
        super().rollback()


class _ManagedCursor:
    """Cursor result facade that does not expose its underlying connection."""

    __slots__ = ("__cursor",)

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self.__cursor = cursor

    @property
    def lastrowid(self) -> int | None:
        return self.__cursor.lastrowid

    @property
    def rowcount(self) -> int:
        return self.__cursor.rowcount

    @property
    def description(self) -> Any:
        return self.__cursor.description

    def fetchone(self) -> sqlite3.Row | tuple[Any, ...] | None:
        return self.__cursor.fetchone()

    def fetchmany(self, size: int | None = None) -> list[Any]:
        return self.__cursor.fetchmany() if size is None else self.__cursor.fetchmany(size)

    def fetchall(self) -> list[Any]:
        return self.__cursor.fetchall()

    def __iter__(self) -> Iterator[Any]:
        while True:
            row = self.__cursor.fetchone()
            if row is None:
                return
            yield row


class ManagedIdentityTransaction:
    """Restricted SQL surface for one runner-owned identity transaction."""

    __slots__ = ("__connection",)

    def __init__(self, connection: _IdentityConnection) -> None:
        self.__connection = connection

    @property
    def in_transaction(self) -> bool:
        return self.__connection.in_transaction

    def execute(
        self,
        sql: str,
        parameters: Sequence[object] | Mapping[str, object] = (),
    ) -> _ManagedCursor:
        return _ManagedCursor(self.__connection.execute(sql, parameters))

    def executemany(
        self,
        sql: str,
        parameters: Iterable[Sequence[object] | Mapping[str, object]],
    ) -> _ManagedCursor:
        return _ManagedCursor(self.__connection.executemany(sql, parameters))

    def query(
        self,
        sql: str,
        parameters: Sequence[object] | Mapping[str, object] = (),
    ) -> list[Any]:
        return self.__connection.execute(sql, parameters).fetchall()


def _deny_transaction_control(
    action: int,
    _arg1: str | None,
    _arg2: str | None,
    _database: str | None,
    _trigger: str | None,
) -> int:
    return (
        sqlite3.SQLITE_DENY
        if action in {sqlite3.SQLITE_TRANSACTION, sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}
        else sqlite3.SQLITE_OK
    )


_UUID_CHECK = (
    "length({column}) = 36 AND substr({column}, 9, 1) = '-' "
    "AND substr({column}, 14, 1) = '-' AND substr({column}, 19, 1) = '-' "
    "AND substr({column}, 24, 1) = '-' AND lower({column}) = {column} "
    "AND length(replace({column}, '-', '')) = 32 "
    "AND replace({column}, '-', '') NOT GLOB '*[^0-9a-f]*'"
)


def _uuid_check(column: str) -> str:
    return _UUID_CHECK.format(column=column)


SCHEMA_SQL = f"""
CREATE TABLE users (
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    email_normalized TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'DISABLED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    disabled_at TEXT
);

CREATE TABLE workspaces (
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    type TEXT NOT NULL CHECK(type IN ('PERSONAL', 'TEAM')),
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    logo_asset_id INTEGER,
    timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'DELETED')),
    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE apps (
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL,
    category TEXT NOT NULL,
    builtin INTEGER NOT NULL CHECK(builtin IN (0, 1)),
    version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'DEPRECATED', 'DISABLED')),
    manifest_checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE roles (
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    builtin INTEGER NOT NULL CHECK(builtin IN (0, 1)),
    system_role TEXT,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'ARCHIVED')),
    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK((builtin = 0 AND system_role IS NULL) OR
          (builtin = 1 AND system_role IN ('OWNER', 'ADMIN', 'MEMBER', 'VIEWER'))),
    UNIQUE(workspace_id, key),
    UNIQUE(id, workspace_id)
);

CREATE TABLE workspace_members (
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    role_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'SUSPENDED', 'LEFT')),
    joined_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(id, workspace_id),
    FOREIGN KEY(role_id, workspace_id) REFERENCES roles(id, workspace_id) ON DELETE RESTRICT
);

CREATE TABLE sessions (
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    csrf_secret_hash TEXT NOT NULL,
    last_workspace_id TEXT REFERENCES workspaces(id) ON DELETE SET NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    revoked_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE workspace_invitations (
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    workspace_id TEXT NOT NULL,
    email_normalized TEXT NOT NULL,
    role_key TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'ACCEPTED', 'REJECTED', 'EXPIRED', 'CANCELED')),
    expires_at TEXT NOT NULL,
    invited_by TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    accepted_by TEXT REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(workspace_id, role_key) REFERENCES roles(workspace_id, key) ON DELETE RESTRICT
);

CREATE TABLE workspace_apps (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE RESTRICT,
    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
    display_order INTEGER NOT NULL CHECK(display_order >= 0),
    settings_json TEXT NOT NULL DEFAULT '{{}}' CHECK(json_valid(settings_json)),
    updated_by TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(workspace_id, app_id)
);

CREATE TABLE permissions (
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    code TEXT NOT NULL UNIQUE,
    app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE RESTRICT,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    owner_only INTEGER NOT NULL CHECK(owner_only IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE role_permissions (
    role_id TEXT NOT NULL REFERENCES roles(id) ON DELETE CASCADE,
    permission_id TEXT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    effect TEXT NOT NULL CHECK(effect = 'ALLOW'),
    created_at TEXT NOT NULL,
    UNIQUE(role_id, permission_id)
);

CREATE TABLE member_permission_overrides (
    workspace_id TEXT NOT NULL,
    membership_id TEXT NOT NULL,
    permission_id TEXT NOT NULL REFERENCES permissions(id) ON DELETE CASCADE,
    effect TEXT NOT NULL CHECK(effect IN ('ALLOW', 'DENY')),
    created_by TEXT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(membership_id, permission_id),
    FOREIGN KEY(membership_id, workspace_id)
        REFERENCES workspace_members(id, workspace_id) ON DELETE CASCADE
);

CREATE TABLE audit_logs (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{{}}' CHECK(json_valid(metadata_json)),
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_workspace_members_active
ON workspace_members(workspace_id, user_id) WHERE status = 'ACTIVE';
CREATE INDEX idx_workspace_members_workspace_status
ON workspace_members(workspace_id, status, role_id);
CREATE INDEX idx_workspace_members_user_status
ON workspace_members(user_id, status, workspace_id);
CREATE INDEX idx_sessions_user_active
ON sessions(user_id, revoked_at, expires_at);
CREATE INDEX idx_workspace_invitations_workspace_status
ON workspace_invitations(workspace_id, status, expires_at);
CREATE UNIQUE INDEX uq_workspace_invitations_pending
ON workspace_invitations(workspace_id, email_normalized) WHERE status = 'PENDING';
CREATE INDEX idx_workspace_apps_app
ON workspace_apps(app_id, workspace_id);
CREATE INDEX idx_roles_workspace_status
ON roles(workspace_id, status, system_role);
CREATE INDEX idx_permissions_app
ON permissions(app_id, code);
CREATE INDEX idx_role_permissions_permission
ON role_permissions(permission_id, role_id);
CREATE INDEX idx_member_overrides_workspace
ON member_permission_overrides(workspace_id, membership_id);
CREATE INDEX idx_audit_logs_workspace_created
ON audit_logs(workspace_id, created_at);

CREATE TRIGGER audit_logs_no_update
BEFORE UPDATE ON audit_logs BEGIN
    SELECT RAISE(ABORT, 'audit logs are append-only');
END;
CREATE TRIGGER audit_logs_no_delete
BEFORE DELETE ON audit_logs BEGIN
    SELECT RAISE(ABORT, 'audit logs are append-only');
END;

CREATE TRIGGER owner_overrides_no_insert
BEFORE INSERT ON member_permission_overrides
WHEN EXISTS (
    SELECT 1 FROM workspace_members AS member
    JOIN roles AS role ON role.id = member.role_id AND role.workspace_id = member.workspace_id
    WHERE member.id = NEW.membership_id AND member.workspace_id = NEW.workspace_id
      AND role.system_role = 'OWNER'
) BEGIN
    SELECT RAISE(ABORT, 'owner memberships cannot have permission overrides');
END;
CREATE TRIGGER owner_overrides_no_update
BEFORE UPDATE OF membership_id, workspace_id ON member_permission_overrides
WHEN EXISTS (
    SELECT 1 FROM workspace_members AS member
    JOIN roles AS role ON role.id = member.role_id AND role.workspace_id = member.workspace_id
    WHERE member.id = NEW.membership_id AND member.workspace_id = NEW.workspace_id
      AND role.system_role = 'OWNER'
) BEGIN
    SELECT RAISE(ABORT, 'owner memberships cannot have permission overrides');
END;
CREATE TRIGGER owner_overrides_no_membership_promotion
BEFORE UPDATE OF role_id, workspace_id ON workspace_members
WHEN EXISTS (
    SELECT 1 FROM roles AS role
    WHERE role.id = NEW.role_id AND role.workspace_id = NEW.workspace_id
      AND role.system_role = 'OWNER'
)
AND EXISTS (
    SELECT 1 FROM member_permission_overrides AS override
    WHERE override.membership_id = OLD.id AND override.workspace_id = OLD.workspace_id
) BEGIN
    SELECT RAISE(ABORT, 'membership with overrides cannot become owner');
END;
CREATE TRIGGER owner_overrides_no_role_promotion
BEFORE UPDATE OF system_role ON roles
WHEN NEW.system_role = 'OWNER' AND OLD.system_role IS NOT 'OWNER'
AND EXISTS (
    SELECT 1 FROM workspace_members AS member
    JOIN member_permission_overrides AS override
      ON override.membership_id = member.id AND override.workspace_id = member.workspace_id
    WHERE member.role_id = OLD.id AND member.workspace_id = OLD.workspace_id
) BEGIN
    SELECT RAISE(ABORT, 'role with overridden memberships cannot become owner');
END;

CREATE TRIGGER owner_only_role_permissions_no_insert
BEFORE INSERT ON role_permissions
WHEN EXISTS (SELECT 1 FROM permissions WHERE id = NEW.permission_id AND owner_only = 1)
AND NOT EXISTS (
    SELECT 1 FROM roles
    WHERE id = NEW.role_id AND builtin = 1 AND system_role = 'OWNER'
) BEGIN
    SELECT RAISE(ABORT, 'owner-only permission requires the built-in Owner role');
END;
CREATE TRIGGER owner_only_role_permissions_no_update
BEFORE UPDATE OF role_id, permission_id ON role_permissions
WHEN EXISTS (SELECT 1 FROM permissions WHERE id = NEW.permission_id AND owner_only = 1)
AND NOT EXISTS (
    SELECT 1 FROM roles
    WHERE id = NEW.role_id AND builtin = 1 AND system_role = 'OWNER'
) BEGIN
    SELECT RAISE(ABORT, 'owner-only permission requires the built-in Owner role');
END;
CREATE TRIGGER owner_only_overrides_no_insert
BEFORE INSERT ON member_permission_overrides
WHEN EXISTS (SELECT 1 FROM permissions WHERE id = NEW.permission_id AND owner_only = 1)
BEGIN
    SELECT RAISE(ABORT, 'owner-only permissions cannot have member overrides');
END;
CREATE TRIGGER owner_only_overrides_no_update
BEFORE UPDATE OF permission_id ON member_permission_overrides
WHEN EXISTS (SELECT 1 FROM permissions WHERE id = NEW.permission_id AND owner_only = 1)
BEGIN
    SELECT RAISE(ABORT, 'owner-only permissions cannot have member overrides');
END;
CREATE TRIGGER owner_only_permission_no_promotion
BEFORE UPDATE OF owner_only ON permissions
WHEN OLD.owner_only = 0 AND NEW.owner_only = 1
AND (
    EXISTS (
        SELECT 1 FROM role_permissions AS binding JOIN roles AS role ON role.id = binding.role_id
        WHERE binding.permission_id = OLD.id
          AND NOT (role.builtin = 1 AND role.system_role = 'OWNER')
    )
    OR EXISTS (
        SELECT 1 FROM member_permission_overrides
        WHERE permission_id = OLD.id
    )
) BEGIN
    SELECT RAISE(ABORT, 'existing bindings are incompatible with owner-only permission');
END;
CREATE TRIGGER owner_only_role_no_demotion
BEFORE UPDATE OF builtin, system_role ON roles
WHEN NOT (NEW.builtin = 1 AND NEW.system_role = 'OWNER')
AND EXISTS (
    SELECT 1 FROM role_permissions AS binding
    JOIN permissions AS permission ON permission.id = binding.permission_id
    WHERE binding.role_id = OLD.id AND permission.owner_only = 1
) BEGIN
    SELECT RAISE(ABORT, 'role with owner-only permissions must remain built-in Owner');
END;

CREATE TRIGGER active_workspace_last_owner_no_delete
BEFORE DELETE ON workspace_members
WHEN OLD.status = 'ACTIVE'
 AND EXISTS (SELECT 1 FROM roles WHERE id = OLD.role_id AND system_role = 'OWNER')
 AND EXISTS (SELECT 1 FROM workspaces WHERE id = OLD.workspace_id AND status = 'ACTIVE')
 AND NOT EXISTS (
    SELECT 1 FROM workspace_members AS member JOIN roles AS role ON role.id = member.role_id
    JOIN users AS owner_user ON owner_user.id = member.user_id
    WHERE member.workspace_id = OLD.workspace_id AND member.id != OLD.id
      AND member.status = 'ACTIVE' AND role.system_role = 'OWNER' AND role.status = 'ACTIVE'
      AND owner_user.status = 'ACTIVE'
 ) BEGIN
    SELECT RAISE(ABORT, 'active workspace must retain an active owner');
END;
CREATE TRIGGER active_workspace_last_owner_no_update
BEFORE UPDATE OF status, role_id, workspace_id ON workspace_members
WHEN OLD.status = 'ACTIVE'
 AND EXISTS (SELECT 1 FROM roles WHERE id = OLD.role_id AND system_role = 'OWNER' AND status = 'ACTIVE')
 AND EXISTS (SELECT 1 FROM workspaces WHERE id = OLD.workspace_id AND status = 'ACTIVE')
 AND (NEW.status != 'ACTIVE' OR NEW.workspace_id != OLD.workspace_id OR NOT EXISTS (
    SELECT 1 FROM roles AS new_role
    WHERE new_role.id = NEW.role_id AND new_role.workspace_id = NEW.workspace_id
      AND new_role.system_role = 'OWNER' AND new_role.status = 'ACTIVE'
 ))
 AND NOT EXISTS (
    SELECT 1 FROM workspace_members AS member JOIN roles AS role ON role.id = member.role_id
    JOIN users AS owner_user ON owner_user.id = member.user_id
    WHERE member.workspace_id = OLD.workspace_id AND member.id != OLD.id
      AND member.status = 'ACTIVE' AND role.system_role = 'OWNER' AND role.status = 'ACTIVE'
      AND owner_user.status = 'ACTIVE'
 ) BEGIN
    SELECT RAISE(ABORT, 'active workspace must retain an active owner');
END;
CREATE TRIGGER active_workspace_owner_role_no_update
BEFORE UPDATE OF system_role, status ON roles
WHEN OLD.system_role = 'OWNER' AND OLD.status = 'ACTIVE'
 AND (NEW.system_role IS NOT 'OWNER' OR NEW.status != 'ACTIVE')
 AND EXISTS (SELECT 1 FROM workspaces WHERE id = OLD.workspace_id AND status = 'ACTIVE')
 AND EXISTS (SELECT 1 FROM workspace_members WHERE workspace_id = OLD.workspace_id AND role_id = OLD.id AND status = 'ACTIVE')
 AND NOT EXISTS (
    SELECT 1 FROM workspace_members AS member JOIN roles AS role ON role.id = member.role_id
    JOIN users AS owner_user ON owner_user.id = member.user_id
    WHERE member.workspace_id = OLD.workspace_id AND role.id != OLD.id
      AND member.status = 'ACTIVE' AND role.system_role = 'OWNER' AND role.status = 'ACTIVE'
      AND owner_user.status = 'ACTIVE'
 ) BEGIN
    SELECT RAISE(ABORT, 'active workspace must retain an active owner');
END;
CREATE TRIGGER active_workspace_owner_on_reactivate
BEFORE UPDATE OF status ON workspaces
WHEN NEW.status = 'ACTIVE' AND OLD.status != 'ACTIVE'
 AND NOT EXISTS (
    SELECT 1 FROM workspace_members AS member JOIN roles AS role ON role.id = member.role_id
    JOIN users AS owner_user ON owner_user.id = member.user_id
    WHERE member.workspace_id = NEW.id AND member.status = 'ACTIVE'
      AND role.system_role = 'OWNER' AND role.status = 'ACTIVE'
      AND owner_user.status = 'ACTIVE'
 ) BEGIN
    SELECT RAISE(ABORT, 'active workspace must have an active owner');
END;
CREATE TRIGGER active_workspace_sole_owner_no_disable
BEFORE UPDATE OF status ON users
WHEN OLD.status = 'ACTIVE' AND NEW.status = 'DISABLED'
AND EXISTS (
    SELECT 1 FROM workspace_members AS owned_member
    JOIN roles AS owned_role ON owned_role.id = owned_member.role_id
      AND owned_role.workspace_id = owned_member.workspace_id
    JOIN workspaces AS workspace ON workspace.id = owned_member.workspace_id
    WHERE owned_member.user_id = OLD.id AND owned_member.status = 'ACTIVE'
      AND owned_role.system_role = 'OWNER' AND owned_role.status = 'ACTIVE'
      AND workspace.status = 'ACTIVE'
      AND NOT EXISTS (
          SELECT 1 FROM workspace_members AS other_member
          JOIN roles AS other_role ON other_role.id = other_member.role_id
            AND other_role.workspace_id = other_member.workspace_id
          JOIN users AS other_user ON other_user.id = other_member.user_id
          WHERE other_member.workspace_id = owned_member.workspace_id
            AND other_member.user_id != OLD.id AND other_member.status = 'ACTIVE'
            AND other_role.system_role = 'OWNER' AND other_role.status = 'ACTIVE'
            AND other_user.status = 'ACTIVE'
      )
) BEGIN
    SELECT RAISE(ABORT, 'sole active owner cannot be disabled');
END;
CREATE TRIGGER disabled_user_sessions_revoke
AFTER UPDATE OF status ON users
WHEN OLD.status != 'DISABLED' AND NEW.status = 'DISABLED'
BEGIN
    UPDATE sessions
    SET revoked_at = COALESCE(revoked_at, NEW.disabled_at, NEW.updated_at)
    WHERE user_id = NEW.id AND revoked_at IS NULL;
END;
"""


AUTH_SCHEMA_SQL = f"""
CREATE TABLE password_reset_tokens (
    id TEXT PRIMARY KEY NOT NULL CHECK({_uuid_check('id')}),
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE CHECK(length(token_hash) = 64 AND token_hash NOT GLOB '*[^0-9a-f]*'),
    expires_at TEXT NOT NULL,
    consumed_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_password_reset_tokens_user_active
ON password_reset_tokens(user_id, consumed_at, expires_at);
"""


PLATFORM_SCHEMA_SQL = """
CREATE TABLE workspace_authorization_versions (
    workspace_id TEXT PRIMARY KEY NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0)
);

CREATE TABLE app_manifests (
    app_id TEXT PRIMARY KEY NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    manifest_json TEXT NOT NULL CHECK(json_valid(manifest_json)),
    updated_at TEXT NOT NULL
);

CREATE TRIGGER authz_version_workspace_apps_insert AFTER INSERT ON workspace_apps BEGIN
    INSERT INTO workspace_authorization_versions VALUES (NEW.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_workspace_apps_update AFTER UPDATE ON workspace_apps BEGIN
    INSERT INTO workspace_authorization_versions VALUES (OLD.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
    INSERT INTO workspace_authorization_versions VALUES (NEW.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_workspace_apps_delete AFTER DELETE ON workspace_apps BEGIN
    INSERT INTO workspace_authorization_versions VALUES (OLD.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_members_insert AFTER INSERT ON workspace_members BEGIN
    INSERT INTO workspace_authorization_versions VALUES (NEW.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_members_update AFTER UPDATE ON workspace_members BEGIN
    INSERT INTO workspace_authorization_versions VALUES (OLD.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
    INSERT INTO workspace_authorization_versions VALUES (NEW.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_members_delete AFTER DELETE ON workspace_members BEGIN
    INSERT INTO workspace_authorization_versions VALUES (OLD.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_roles_insert AFTER INSERT ON roles BEGIN
    INSERT INTO workspace_authorization_versions VALUES (NEW.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_roles_update AFTER UPDATE ON roles BEGIN
    INSERT INTO workspace_authorization_versions VALUES (OLD.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
    INSERT INTO workspace_authorization_versions VALUES (NEW.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_roles_delete AFTER DELETE ON roles BEGIN
    INSERT INTO workspace_authorization_versions VALUES (OLD.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_role_permissions_insert AFTER INSERT ON role_permissions BEGIN
    INSERT INTO workspace_authorization_versions
    SELECT workspace_id, 1 FROM roles WHERE id=NEW.role_id AND true
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_role_permissions_delete AFTER DELETE ON role_permissions BEGIN
    INSERT INTO workspace_authorization_versions
    SELECT workspace_id, 1 FROM roles WHERE id=OLD.role_id AND true
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_overrides_insert AFTER INSERT ON member_permission_overrides BEGIN
    INSERT INTO workspace_authorization_versions VALUES (NEW.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_overrides_update AFTER UPDATE ON member_permission_overrides BEGIN
    INSERT INTO workspace_authorization_versions VALUES (OLD.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
    INSERT INTO workspace_authorization_versions VALUES (NEW.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_overrides_delete AFTER DELETE ON member_permission_overrides BEGIN
    INSERT INTO workspace_authorization_versions VALUES (OLD.workspace_id, 1)
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
"""


PLATFORM_V4_SCHEMA_SQL = """
CREATE TRIGGER authz_version_role_permissions_update AFTER UPDATE ON role_permissions BEGIN
    INSERT INTO workspace_authorization_versions
    SELECT workspace_id, 1 FROM roles WHERE id=OLD.role_id AND true
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
    INSERT INTO workspace_authorization_versions
    SELECT workspace_id, 1 FROM roles WHERE id=NEW.role_id AND true
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_apps_status_update AFTER UPDATE OF status ON apps
WHEN OLD.status != NEW.status BEGIN
    INSERT INTO workspace_authorization_versions(workspace_id, version)
    SELECT workspace_id, 1 FROM workspace_apps WHERE app_id=NEW.id
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
CREATE TRIGGER authz_version_permissions_update AFTER UPDATE OF owner_only, code, app_id ON permissions
WHEN OLD.owner_only != NEW.owner_only OR OLD.code != NEW.code OR OLD.app_id != NEW.app_id BEGIN
    INSERT INTO workspace_authorization_versions(workspace_id, version)
    SELECT DISTINCT role.workspace_id, 1 FROM role_permissions AS binding
    JOIN roles AS role ON role.id=binding.role_id WHERE binding.permission_id=NEW.id
    UNION SELECT DISTINCT override.workspace_id, 1 FROM member_permission_overrides AS override
    WHERE override.permission_id=NEW.id AND true
    ON CONFLICT(workspace_id) DO UPDATE SET version=version+1;
END;
"""


PLATFORM_V5_SCHEMA_SQL = """
CREATE TABLE workspace_favorites (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    resource_type TEXT NOT NULL CHECK(length(resource_type) BETWEEN 1 AND 64),
    resource_id TEXT NOT NULL CHECK(length(resource_id) BETWEEN 1 AND 256),
    label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 256),
    href TEXT NOT NULL CHECK(length(href) BETWEEN 1 AND 1024),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, user_id, resource_type, resource_id)
);

CREATE TABLE workspace_recent_views (
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    resource_type TEXT NOT NULL CHECK(length(resource_type) BETWEEN 1 AND 64),
    resource_id TEXT NOT NULL CHECK(length(resource_id) BETWEEN 1 AND 256),
    label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 256),
    href TEXT NOT NULL CHECK(length(href) BETWEEN 1 AND 1024),
    viewed_at TEXT NOT NULL,
    PRIMARY KEY(workspace_id, user_id, resource_type, resource_id)
);

CREATE INDEX idx_workspace_favorites_user_updated
ON workspace_favorites(workspace_id, user_id, updated_at DESC);
CREATE INDEX idx_workspace_recent_user_viewed
ON workspace_recent_views(workspace_id, user_id, viewed_at DESC);
"""


PLATFORM_V6_SCHEMA_SQL = """
CREATE TABLE dashboard_widgets (
    key TEXT PRIMARY KEY NOT NULL CHECK(length(key) BETWEEN 1 AND 128),
    app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,
    manifest_checksum TEXT NOT NULL CHECK(length(manifest_checksum) = 64 AND manifest_checksum NOT GLOB '*[^0-9a-f]*'),
    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'DISABLED')),
    updated_at TEXT NOT NULL
);

CREATE TABLE user_dashboard_widgets (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    widget_key TEXT NOT NULL REFERENCES dashboard_widgets(key) ON DELETE CASCADE,
    enabled INTEGER NOT NULL CHECK(enabled IN (0, 1)),
    display_order INTEGER NOT NULL CHECK(display_order >= 0),
    size TEXT NOT NULL CHECK(size IN ('SMALL', 'MEDIUM', 'LARGE')),
    settings_json TEXT NOT NULL CHECK(json_valid(settings_json)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(user_id, workspace_id, widget_key)
);

CREATE INDEX idx_user_dashboard_widgets_layout
ON user_dashboard_widgets(user_id, workspace_id, enabled, display_order);
"""


PLATFORM_V7_SCHEMA_SQL = """
CREATE TABLE activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    app_key TEXT NOT NULL REFERENCES apps(key) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK(length(action) BETWEEN 1 AND 128),
    resource_type TEXT NOT NULL CHECK(length(resource_type) BETWEEN 1 AND 64),
    resource_id TEXT NOT NULL CHECK(length(resource_id) BETWEEN 1 AND 256),
    label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 256),
    href TEXT NOT NULL CHECK(length(href) BETWEEN 1 AND 1024),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json)),
    created_at TEXT NOT NULL
);

CREATE INDEX idx_activity_logs_workspace_created
ON activity_logs(workspace_id, created_at DESC, id DESC);
"""


PLATFORM_V8_SCHEMA_SQL = """
CREATE TABLE activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    actor_user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
    app_key TEXT NOT NULL REFERENCES apps(key) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK(length(action) BETWEEN 1 AND 128),
    resource_type TEXT NOT NULL CHECK(length(resource_type) BETWEEN 1 AND 64),
    resource_id TEXT NOT NULL CHECK(length(resource_id) BETWEEN 1 AND 256),
    metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json)),
    created_at TEXT NOT NULL
);

CREATE INDEX idx_activity_logs_workspace_created
ON activity_logs(workspace_id, created_at DESC, id DESC);
"""


V1_REQUIRED_COLUMNS = {
    "users": ("id", "email_normalized", "display_name", "password_hash", "status", "created_at", "updated_at", "disabled_at"),
    "workspaces": ("id", "type", "name", "description", "logo_asset_id", "timezone", "status", "created_by", "created_at", "updated_at", "deleted_at"),
    "workspace_members": ("id", "workspace_id", "user_id", "role_id", "status", "joined_at", "created_at", "updated_at"),
    "sessions": ("id", "user_id", "token_hash", "csrf_secret_hash", "last_workspace_id", "expires_at", "last_seen_at", "revoked_at", "created_at"),
    "workspace_invitations": ("id", "workspace_id", "email_normalized", "role_key", "token_hash", "status", "expires_at", "invited_by", "accepted_by", "created_at", "updated_at"),
    "apps": ("id", "key", "name", "description", "icon", "category", "builtin", "version", "status", "manifest_checksum", "created_at", "updated_at"),
    "workspace_apps": ("workspace_id", "app_id", "enabled", "display_order", "settings_json", "updated_by", "created_at", "updated_at"),
    "roles": ("id", "workspace_id", "key", "name", "description", "builtin", "system_role", "status", "created_by", "created_at", "updated_at"),
    "permissions": ("id", "code", "app_id", "name", "description", "owner_only", "created_at", "updated_at"),
    "role_permissions": ("role_id", "permission_id", "effect", "created_at"),
    "member_permission_overrides": ("workspace_id", "membership_id", "permission_id", "effect", "created_by", "created_at", "updated_at"),
    "audit_logs": ("workspace_id", "actor_user_id", "action", "target_type", "target_id", "metadata_json", "created_at"),
}

REQUIRED_COLUMNS = {
    **V1_REQUIRED_COLUMNS,
    "password_reset_tokens": (
        "id", "user_id", "token_hash", "expires_at", "consumed_at", "created_at"
    ),
}

V2_REQUIRED_COLUMNS = dict(REQUIRED_COLUMNS)
REQUIRED_COLUMNS.update(
    {
        "workspace_authorization_versions": ("workspace_id", "version"),
        "app_manifests": ("app_id", "manifest_json", "updated_at"),
    }
)
V3_REQUIRED_COLUMNS = dict(REQUIRED_COLUMNS)

V1_REQUIRED_INDEXES = {
    "uq_workspace_members_active",
    "idx_workspace_members_workspace_status",
    "idx_workspace_members_user_status",
    "idx_sessions_user_active",
    "idx_workspace_invitations_workspace_status",
    "uq_workspace_invitations_pending",
    "idx_workspace_apps_app",
    "idx_roles_workspace_status",
    "idx_permissions_app",
    "idx_role_permissions_permission",
    "idx_member_overrides_workspace",
    "idx_audit_logs_workspace_created",
}
REQUIRED_INDEXES = V1_REQUIRED_INDEXES | {"idx_password_reset_tokens_user_active"}
V2_REQUIRED_INDEXES = set(REQUIRED_INDEXES)
V3_REQUIRED_INDEXES = set(REQUIRED_INDEXES)

REQUIRED_TRIGGERS = {
    "audit_logs_no_update", "audit_logs_no_delete", "owner_overrides_no_insert",
    "owner_overrides_no_update", "owner_overrides_no_membership_promotion",
    "owner_overrides_no_role_promotion", "active_workspace_last_owner_no_delete",
    "owner_only_role_permissions_no_insert", "owner_only_role_permissions_no_update",
    "owner_only_overrides_no_insert", "owner_only_overrides_no_update",
    "owner_only_permission_no_promotion", "owner_only_role_no_demotion",
    "active_workspace_last_owner_no_update", "active_workspace_owner_role_no_update",
    "active_workspace_owner_on_reactivate", "active_workspace_sole_owner_no_disable",
    "disabled_user_sessions_revoke",
}
V2_REQUIRED_TRIGGERS = set(REQUIRED_TRIGGERS)
REQUIRED_TRIGGERS.update(
    {
        "authz_version_workspace_apps_insert", "authz_version_workspace_apps_update",
        "authz_version_workspace_apps_delete", "authz_version_members_insert",
        "authz_version_members_update", "authz_version_members_delete",
        "authz_version_roles_insert", "authz_version_roles_update",
        "authz_version_roles_delete", "authz_version_role_permissions_insert",
        "authz_version_role_permissions_delete", "authz_version_overrides_insert",
        "authz_version_overrides_update", "authz_version_overrides_delete",
    }
)
V3_REQUIRED_TRIGGERS = set(REQUIRED_TRIGGERS)
REQUIRED_TRIGGERS.update(
    {"authz_version_role_permissions_update", "authz_version_apps_status_update", "authz_version_permissions_update"}
)

V4_REQUIRED_COLUMNS = dict(REQUIRED_COLUMNS)
V4_REQUIRED_INDEXES = set(REQUIRED_INDEXES)
V4_REQUIRED_TRIGGERS = set(REQUIRED_TRIGGERS)

REQUIRED_COLUMNS.update(
    {
        "workspace_favorites": (
            "workspace_id", "user_id", "app_id", "resource_type", "resource_id",
            "label", "href", "created_at", "updated_at",
        ),
        "workspace_recent_views": (
            "workspace_id", "user_id", "app_id", "resource_type", "resource_id",
            "label", "href", "viewed_at",
        ),
    }
)
REQUIRED_INDEXES.update(
    {"idx_workspace_favorites_user_updated", "idx_workspace_recent_user_viewed"}
)
REQUIRED_CHECK_FRAGMENTS = {
    "users": ("statusin('active','disabled')", "length(id)=36"),
    "workspaces": ("typein('personal','team')", "statusin('active','deleted')"),
    "workspace_members": ("statusin('active','suspended','left')",),
    "workspace_invitations": ("statusin('pending','accepted','rejected','expired','canceled')",),
    "apps": ("builtinin(0,1)", "statusin('active','deprecated','disabled')"),
    "workspace_apps": ("enabledin(0,1)", "display_order>=0", "json_valid(settings_json)"),
    "roles": ("builtinin(0,1)", "builtin=0andsystem_roleisnull", "builtin=1andsystem_rolein('owner','admin','member','viewer')", "statusin('active','archived')"),
    "permissions": ("owner_onlyin(0,1)",),
    "role_permissions": ("effect='allow'",),
    "member_permission_overrides": ("effectin('allow','deny')",),
    "audit_logs": ("json_valid(metadata_json)",),
}
V2_REQUIRED_CHECK_FRAGMENTS = dict(REQUIRED_CHECK_FRAGMENTS)
REQUIRED_CHECK_FRAGMENTS.update(
    {
        "workspace_authorization_versions": ("version>=0",),
        "app_manifests": ("json_valid(manifest_json)",),
    }
)

V5_REQUIRED_COLUMNS = dict(REQUIRED_COLUMNS)
V5_REQUIRED_INDEXES = set(REQUIRED_INDEXES)
V5_REQUIRED_TRIGGERS = set(REQUIRED_TRIGGERS)
V5_REQUIRED_CHECK_FRAGMENTS = dict(REQUIRED_CHECK_FRAGMENTS)

REQUIRED_COLUMNS.update(
    {
        "dashboard_widgets": (
            "key", "app_id", "manifest_checksum", "status", "updated_at",
        ),
        "user_dashboard_widgets": (
            "user_id", "workspace_id", "widget_key", "enabled", "display_order",
            "size", "settings_json", "created_at", "updated_at",
        ),
    }
)
REQUIRED_INDEXES.add("idx_user_dashboard_widgets_layout")
REQUIRED_CHECK_FRAGMENTS.update(
    {
        "dashboard_widgets": (
            "length(key)between1and128", "length(manifest_checksum)=64",
            "statusin('active','disabled')",
        ),
        "user_dashboard_widgets": (
            "enabledin(0,1)", "display_order>=0",
            "sizein('small','medium','large')", "json_valid(settings_json)",
        ),
    }
)

V6_REQUIRED_COLUMNS = dict(REQUIRED_COLUMNS)
V6_REQUIRED_INDEXES = set(REQUIRED_INDEXES)
V6_REQUIRED_TRIGGERS = set(REQUIRED_TRIGGERS)

REQUIRED_COLUMNS["activity_logs"] = (
    "id", "workspace_id", "actor_user_id", "app_key", "action",
    "resource_type", "resource_id", "label", "href", "metadata_json", "created_at",
)
REQUIRED_INDEXES.add("idx_activity_logs_workspace_created")
REQUIRED_CHECK_FRAGMENTS["activity_logs"] = (
    "length(action)between1and128", "length(resource_type)between1and64",
    "length(resource_id)between1and256", "length(label)between1and256",
    "length(href)between1and1024", "json_valid(metadata_json)",
)
V3_REQUIRED_CHECK_FRAGMENTS = dict(REQUIRED_CHECK_FRAGMENTS)
V4_REQUIRED_CHECK_FRAGMENTS = dict(REQUIRED_CHECK_FRAGMENTS)
REQUIRED_CHECK_FRAGMENTS.update(
    {
        "workspace_favorites": (
            "length(resource_type)between1and64", "length(resource_id)between1and256",
            "length(label)between1and256", "length(href)between1and1024",
        ),
        "workspace_recent_views": (
            "length(resource_type)between1and64", "length(resource_id)between1and256",
            "length(label)between1and256", "length(href)between1and1024",
        ),
    }
)
V6_REQUIRED_CHECK_FRAGMENTS = {
    key: value for key, value in REQUIRED_CHECK_FRAGMENTS.items()
    if key != "activity_logs"
}

V7_REQUIRED_COLUMNS = dict(REQUIRED_COLUMNS)
V7_REQUIRED_INDEXES = set(REQUIRED_INDEXES)
V7_REQUIRED_TRIGGERS = set(REQUIRED_TRIGGERS)
V7_REQUIRED_CHECK_FRAGMENTS = dict(REQUIRED_CHECK_FRAGMENTS)

REQUIRED_COLUMNS["activity_logs"] = (
    "id", "workspace_id", "actor_user_id", "app_key", "action",
    "resource_type", "resource_id", "metadata_json", "created_at",
)
REQUIRED_CHECK_FRAGMENTS["activity_logs"] = (
    "length(action)between1and128", "length(resource_type)between1and64",
    "length(resource_id)between1and256", "json_valid(metadata_json)",
)

TIMESTAMP_COLUMNS = {
    "users": ("created_at", "updated_at", "disabled_at"),
    "workspaces": ("created_at", "updated_at", "deleted_at"),
    "workspace_members": ("joined_at", "created_at", "updated_at"),
    "sessions": ("expires_at", "last_seen_at", "revoked_at", "created_at"),
    "workspace_invitations": ("expires_at", "created_at", "updated_at"),
    "apps": ("created_at", "updated_at"),
    "workspace_apps": ("created_at", "updated_at"),
    "roles": ("created_at", "updated_at"),
    "permissions": ("created_at", "updated_at"),
    "role_permissions": ("created_at",),
    "member_permission_overrides": ("created_at", "updated_at"),
    "audit_logs": ("created_at",),
    "workspace_favorites": ("created_at", "updated_at"),
    "workspace_recent_views": ("viewed_at",),
    "password_reset_tokens": ("expires_at", "consumed_at", "created_at"),
    "app_manifests": ("updated_at",),
    "dashboard_widgets": ("updated_at",),
    "user_dashboard_widgets": ("created_at", "updated_at"),
    "activity_logs": ("created_at",),
}
_UTC_RFC3339 = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|\+00:00)$"
)


def resolve_database_path(database_path: str | Path | None = None) -> Path:
    if database_path is not None:
        return Path(database_path).expanduser().resolve()
    configured = os.getenv("IDENTITY_DB_PATH")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DB_PATH


def _configure(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")


def _compact_sql(value: str) -> str:
    return "".join(value.lower().split()).rstrip(";")


def _schema_statements(sql: str = SCHEMA_SQL) -> tuple[str, ...]:
    statements: list[str] = []
    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            statements.append(pending.strip())
            pending = ""
    if pending.strip():
        raise IdentityDatabaseError("identity v1 schema contains incomplete SQL")
    return tuple(statements)


def _expected_schema_objects(*sources: str) -> dict[str, str]:
    expected: dict[str, str] = {}
    for source in sources or (SCHEMA_SQL,):
      for statement in _schema_statements(source):
        words = statement.split()
        if len(words) < 3 or words[0].upper() != "CREATE":
            raise IdentityDatabaseError("identity v1 schema contains unsupported DDL")
        name_index = 3 if words[1].upper() == "UNIQUE" else 2
        expected[words[name_index].strip('"`[').rstrip("]")] = _compact_sql(statement)
    return expected


def _verify_v1_schema(connection: sqlite3.Connection, _context: object = None) -> None:
    if get_user_version(connection) != 1:
        raise IdentityDatabaseError("identity database user_version must be 1")
    actual_objects = {
        row[0]: _compact_sql(row[1])
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        )
    }
    expected_objects = _expected_schema_objects(SCHEMA_SQL)
    if actual_objects != expected_objects:
        raise IdentityDatabaseError("identity v1 schema object set is incompatible")
    _verify_semantic_integrity(connection, include_auth=False)


def _verify_schema_contract(
    connection: sqlite3.Connection,
    *,
    version: int,
    columns: Mapping[str, tuple[str, ...]],
    indexes_required: set[str],
    triggers_required: set[str],
    check_fragments: Mapping[str, tuple[str, ...]],
    schema_sources: tuple[str, ...],
    include_platform: bool,
    validate_builtin_roles: bool = False,
) -> None:
    if get_user_version(connection) != version:
        raise IdentityDatabaseError(f"identity database user_version must be {version}")
    tables = {
        row[0]: row[1]
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if set(tables) != set(columns):
        raise IdentityDatabaseError("identity database table set does not match schema")
    for table, expected in columns.items():
        actual = tuple(row[1] for row in connection.execute(f'PRAGMA table_info("{table}")'))
        if actual != expected:
            raise IdentityDatabaseError(f"identity table {table} has incompatible columns")
        compact = _compact_sql(tables[table])
        for fragment in check_fragments.get(table, ()):
            if fragment not in compact:
                raise IdentityDatabaseError(f"identity table {table} is missing a required CHECK")
    indexes = {
        row[0]: row[1] or ""
        for row in connection.execute("SELECT name, sql FROM sqlite_master WHERE type='index'")
    }
    missing_indexes = indexes_required - indexes.keys()
    if missing_indexes:
        raise IdentityDatabaseError(f"identity database is missing indexes: {sorted(missing_indexes)}")
    active_index = _compact_sql(indexes["uq_workspace_members_active"])
    if "uniqueindex" not in active_index or "wherestatus='active'" not in active_index:
        raise IdentityDatabaseError("ACTIVE membership partial uniqueness is incompatible")
    triggers = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
    }
    if not triggers_required.issubset(triggers):
        raise IdentityDatabaseError("identity database is missing integrity triggers")
    actual_objects = {
        row[0]: _compact_sql(row[1])
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%'"
        )
    }
    expected_objects = _expected_schema_objects(*schema_sources)
    if actual_objects.keys() != expected_objects.keys():
        raise IdentityDatabaseError("identity SQL-bearing schema object set is incompatible")
    for name, expected_sql in expected_objects.items():
        if actual_objects[name] != expected_sql:
            raise IdentityDatabaseError(f"identity schema object {name} is incompatible")
    _verify_semantic_integrity(
        connection,
        include_platform=include_platform,
        validate_builtin_roles=validate_builtin_roles,
    )


def _verify_v2_schema(connection: sqlite3.Connection, _context: object = None) -> None:
    _verify_schema_contract(
        connection,
        version=2,
        columns=V2_REQUIRED_COLUMNS,
        indexes_required=V2_REQUIRED_INDEXES,
        triggers_required=V2_REQUIRED_TRIGGERS,
        check_fragments=V2_REQUIRED_CHECK_FRAGMENTS,
        schema_sources=(SCHEMA_SQL, AUTH_SCHEMA_SQL),
        include_platform=False,
    )


def _verify_schema(connection: sqlite3.Connection, _context: object = None) -> None:
    _verify_schema_contract(
        connection,
        version=IDENTITY_SCHEMA_VERSION,
        columns=REQUIRED_COLUMNS,
        indexes_required=REQUIRED_INDEXES,
        triggers_required=REQUIRED_TRIGGERS,
        check_fragments=REQUIRED_CHECK_FRAGMENTS,
        schema_sources=(SCHEMA_SQL, AUTH_SCHEMA_SQL, PLATFORM_SCHEMA_SQL, PLATFORM_V4_SCHEMA_SQL, PLATFORM_V5_SCHEMA_SQL, PLATFORM_V6_SCHEMA_SQL, PLATFORM_V8_SCHEMA_SQL),
        include_platform=True,
        validate_builtin_roles=True,
    )


def _verify_v6_schema(connection: sqlite3.Connection, _context: object = None) -> None:
    _verify_schema_contract(
        connection,
        version=6,
        columns=V6_REQUIRED_COLUMNS,
        indexes_required=V6_REQUIRED_INDEXES,
        triggers_required=V6_REQUIRED_TRIGGERS,
        check_fragments=V6_REQUIRED_CHECK_FRAGMENTS,
        schema_sources=(SCHEMA_SQL, AUTH_SCHEMA_SQL, PLATFORM_SCHEMA_SQL, PLATFORM_V4_SCHEMA_SQL, PLATFORM_V5_SCHEMA_SQL, PLATFORM_V6_SCHEMA_SQL),
        include_platform=True,
        validate_builtin_roles=True,
    )


def _verify_v7_schema(connection: sqlite3.Connection, _context: object = None) -> None:
    _verify_schema_contract(
        connection,
        version=7,
        columns=V7_REQUIRED_COLUMNS,
        indexes_required=V7_REQUIRED_INDEXES,
        triggers_required=V7_REQUIRED_TRIGGERS,
        check_fragments=V7_REQUIRED_CHECK_FRAGMENTS,
        schema_sources=(SCHEMA_SQL, AUTH_SCHEMA_SQL, PLATFORM_SCHEMA_SQL, PLATFORM_V4_SCHEMA_SQL, PLATFORM_V5_SCHEMA_SQL, PLATFORM_V6_SCHEMA_SQL, PLATFORM_V7_SCHEMA_SQL),
        include_platform=True,
        validate_builtin_roles=True,
    )


def _verify_v3_schema(connection: sqlite3.Connection, _context: object = None) -> None:
    _verify_schema_contract(
        connection,
        version=3,
        columns=V3_REQUIRED_COLUMNS,
        indexes_required=V3_REQUIRED_INDEXES,
        triggers_required=V3_REQUIRED_TRIGGERS,
        check_fragments=V3_REQUIRED_CHECK_FRAGMENTS,
        schema_sources=(SCHEMA_SQL, AUTH_SCHEMA_SQL, PLATFORM_SCHEMA_SQL),
        include_platform=True,
    )


def _verify_v4_schema(connection: sqlite3.Connection, _context: object = None) -> None:
    _verify_schema_contract(
        connection,
        version=4,
        columns=V4_REQUIRED_COLUMNS,
        indexes_required=V4_REQUIRED_INDEXES,
        triggers_required=V4_REQUIRED_TRIGGERS,
        check_fragments=V4_REQUIRED_CHECK_FRAGMENTS,
        schema_sources=(SCHEMA_SQL, AUTH_SCHEMA_SQL, PLATFORM_SCHEMA_SQL, PLATFORM_V4_SCHEMA_SQL),
        include_platform=True,
        validate_builtin_roles=True,
    )


def _verify_v5_schema(connection: sqlite3.Connection, _context: object = None) -> None:
    _verify_schema_contract(
        connection,
        version=5,
        columns=V5_REQUIRED_COLUMNS,
        indexes_required=V5_REQUIRED_INDEXES,
        triggers_required=V5_REQUIRED_TRIGGERS,
        check_fragments=V5_REQUIRED_CHECK_FRAGMENTS,
        schema_sources=(SCHEMA_SQL, AUTH_SCHEMA_SQL, PLATFORM_SCHEMA_SQL, PLATFORM_V4_SCHEMA_SQL, PLATFORM_V5_SCHEMA_SQL),
        include_platform=True,
        validate_builtin_roles=True,
    )


def _verify_semantic_integrity(
    connection: sqlite3.Connection, *, include_auth: bool = True,
    include_platform: bool = True, validate_builtin_roles: bool = False
) -> None:
    ownerless = connection.execute(
        "SELECT workspace.id FROM workspaces AS workspace "
        "WHERE workspace.status='ACTIVE' AND NOT EXISTS ("
        "SELECT 1 FROM workspace_members AS member JOIN roles AS role "
        "ON role.id=member.role_id AND role.workspace_id=member.workspace_id "
        "JOIN users AS owner_user ON owner_user.id=member.user_id "
        "WHERE member.workspace_id=workspace.id AND member.status='ACTIVE' "
        "AND role.status='ACTIVE' AND role.system_role='OWNER' "
        "AND owner_user.status='ACTIVE') LIMIT 1"
    ).fetchone()
    if ownerless:
        raise IdentityDatabaseError("active workspace has no active owner")
    owner_override = connection.execute(
        "SELECT 1 FROM member_permission_overrides AS override "
        "JOIN workspace_members AS member ON member.id=override.membership_id "
        "AND member.workspace_id=override.workspace_id "
        "JOIN roles AS role ON role.id=member.role_id AND role.workspace_id=member.workspace_id "
        "WHERE role.system_role='OWNER' LIMIT 1"
    ).fetchone()
    if owner_override:
        raise IdentityDatabaseError("owner membership has a permission override")
    invalid_role = connection.execute(
        "SELECT 1 FROM roles WHERE "
        "(builtin=0 AND system_role IS NOT NULL) OR "
        "(builtin=1 AND (system_role IS NULL OR "
        "system_role NOT IN ('OWNER','ADMIN','MEMBER','VIEWER'))) OR "
        "builtin NOT IN (0,1) LIMIT 1"
    ).fetchone()
    if invalid_role:
        raise IdentityDatabaseError("role builtin/system_role contract is invalid")
    if validate_builtin_roles:
        from .foundation import BUILTIN_ROLES
        expected_builtin_roles = {(key, name, system_role) for key, name, system_role in BUILTIN_ROLES}
        for workspace in connection.execute("SELECT id FROM workspaces"):
            actual_builtin_roles = {
                (row[0], row[1], row[2])
                for row in connection.execute(
                    "SELECT key, name, system_role FROM roles "
                    "WHERE workspace_id=? AND builtin=1 AND status='ACTIVE'",
                    (workspace[0],),
                )
            }
            if actual_builtin_roles != expected_builtin_roles:
                raise IdentityDatabaseError("built-in role definitions are incompatible")
    invalid_owner_only_role = connection.execute(
        "SELECT 1 FROM role_permissions AS binding "
        "JOIN permissions AS permission ON permission.id=binding.permission_id "
        "JOIN roles AS role ON role.id=binding.role_id "
        "WHERE permission.owner_only=1 "
        "AND NOT (role.builtin=1 AND role.system_role='OWNER') LIMIT 1"
    ).fetchone()
    if invalid_owner_only_role:
        raise IdentityDatabaseError(
            "owner-only permission is bound outside the built-in Owner role"
        )
    invalid_owner_only_override = connection.execute(
        "SELECT 1 FROM member_permission_overrides AS override "
        "JOIN permissions AS permission ON permission.id=override.permission_id "
        "WHERE permission.owner_only=1 LIMIT 1"
    ).fetchone()
    if invalid_owner_only_override:
        raise IdentityDatabaseError("owner-only permission has a member override")
    for table in ("users", "workspace_invitations"):
        for row in connection.execute(f"SELECT email_normalized FROM {table}"):
            value = row[0]
            try:
                canonical = normalize_email(value)
            except ValueError as error:
                raise IdentityDatabaseError(
                    f"{table} contains an invalid normalized email"
                ) from error
            if value != canonical:
                raise IdentityDatabaseError(
                    f"{table} contains a noncanonical normalized email"
                )
    timestamp_columns = TIMESTAMP_COLUMNS if include_auth else {
        key: value for key, value in TIMESTAMP_COLUMNS.items()
        if key not in {"password_reset_tokens", "app_manifests"}
    }
    if not include_platform:
        timestamp_columns = {
            key: value for key, value in timestamp_columns.items()
            if key != "app_manifests"
        }
    existing_tables = {
        row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table, columns in timestamp_columns.items():
        if table not in existing_tables:
            continue
        projection = ", ".join(columns)
        for row in connection.execute(f"SELECT {projection} FROM {table}"):
            for column, value in zip(columns, row, strict=True):
                if value is None:
                    continue
                if not isinstance(value, str) or not _UTC_RFC3339.fullmatch(value):
                    raise IdentityDatabaseError(
                        f"{table}.{column} must be UTC RFC3339"
                    )
                try:
                    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError as error:
                    raise IdentityDatabaseError(
                        f"{table}.{column} must be UTC RFC3339"
                    ) from error
                if parsed.utcoffset() != timedelta(0):
                    raise IdentityDatabaseError(
                        f"{table}.{column} must be UTC RFC3339"
                    )


def _apply_v1(connection: sqlite3.Connection, _context: object) -> None:
    """Execute complete DDL statements without escaping the runner transaction."""

    for statement in _schema_statements():
        connection.execute(statement)


def _apply_v2(connection: sqlite3.Connection, _context: object) -> None:
    for statement in _schema_statements(AUTH_SCHEMA_SQL):
        connection.execute(statement)


def _apply_v3(connection: sqlite3.Connection, _context: object) -> None:
    for statement in _schema_statements(PLATFORM_SCHEMA_SQL):
        connection.execute(statement)
    connection.execute(
        "INSERT INTO workspace_authorization_versions(workspace_id, version) "
        "SELECT id, 0 FROM workspaces WHERE true ON CONFLICT(workspace_id) DO NOTHING"
    )


def _apply_v4(connection: sqlite3.Connection, _context: object) -> None:
    for statement in _schema_statements(PLATFORM_V4_SCHEMA_SQL):
        connection.execute(statement)
    from .foundation import BUILTIN_ROLES
    for workspace in connection.execute("SELECT id, created_by, created_at FROM workspaces"):
        for key, name, system_role in BUILTIN_ROLES:
            connection.execute(
                "INSERT OR IGNORE INTO roles(id, workspace_id, key, name, description, builtin, "
                "system_role, status, created_by, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, '', 1, ?, 'ACTIVE', ?, ?, ?)",
                (
                    str(uuid5(NAMESPACE_URL, f"my-workspace:migrated-role:{workspace[0]}:{key}")),
                    workspace[0], key, name, system_role, workspace[1], workspace[2], workspace[2],
                ),
            )


def _apply_v5(connection: sqlite3.Connection, _context: object) -> None:
    for statement in _schema_statements(PLATFORM_V5_SCHEMA_SQL):
        connection.execute(statement)


def _apply_v6(connection: sqlite3.Connection, _context: object) -> None:
    for statement in _schema_statements(PLATFORM_V6_SCHEMA_SQL):
        connection.execute(statement)


def _apply_v7(connection: sqlite3.Connection, _context: object) -> None:
    for statement in _schema_statements(PLATFORM_V7_SCHEMA_SQL):
        connection.execute(statement)


def _apply_v8(connection: sqlite3.Connection, _context: object) -> None:
    rows = connection.execute(
        "SELECT id,workspace_id,actor_user_id,app_key,action,resource_type,"
        "resource_id,metadata_json,created_at FROM activity_logs ORDER BY id"
    ).fetchall()
    connection.execute("DROP TABLE activity_logs")
    for statement in _schema_statements(PLATFORM_V8_SCHEMA_SQL):
        connection.execute(statement)
    connection.executemany(
        "INSERT INTO activity_logs(id,workspace_id,actor_user_id,app_key,action,"
        "resource_type,resource_id,metadata_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        rows,
    )


MIGRATIONS = (
    MigrationStep(0, 1, _apply_v1, _verify_v1_schema),
    MigrationStep(1, 2, _apply_v2, _verify_v2_schema),
    MigrationStep(2, 3, _apply_v3, _verify_v3_schema),
    MigrationStep(3, 4, _apply_v4, _verify_v4_schema),
    MigrationStep(4, 5, _apply_v5, _verify_v5_schema),
    MigrationStep(5, 6, _apply_v6, _verify_v6_schema),
    MigrationStep(6, 7, _apply_v7, _verify_v7_schema),
    MigrationStep(7, 8, _apply_v8, _verify_schema),
)


def initialize_database(database_path: str | Path | None = None) -> Path:
    path = resolve_database_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        _configure(connection)
        if get_user_version(connection) == 0:
            tables = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
            ).fetchone()
            if tables:
                raise IdentityDatabaseError("identity database has an unversioned schema")
        try:
            migrate(connection, target_version=IDENTITY_SCHEMA_VERSION, migrations=MIGRATIONS)
        except MigrationError as error:
            raise IdentityDatabaseError("identity database migration failed") from error
    verify_database(path)
    return path


def verify_database(database_path: str | Path | None = None) -> None:
    path = resolve_database_path(database_path)
    if not path.is_file():
        raise IdentityDatabaseError(f"identity database does not exist: {path}")
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        _configure(connection)
        try:
            verify_sqlite_database(connection)
            _verify_schema(connection)
        except (RuntimeError, sqlite3.DatabaseError) as error:
            if isinstance(error, IdentityDatabaseError):
                raise
            raise IdentityDatabaseError("identity database verification failed") from error


@contextmanager
def database_connection(
    database_path: str | Path | None = None,
) -> Iterator[ManagedIdentityTransaction]:
    connection = sqlite3.connect(
        resolve_database_path(database_path), factory=_IdentityConnection
    )
    _configure(connection)
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection._boundary_owned = True
        connection.set_authorizer(_deny_transaction_control)
        yield ManagedIdentityTransaction(connection)
        connection.set_authorizer(None)
        connection._boundary_owned = False
        _verify_semantic_integrity(connection)
        connection.commit()
    except Exception:
        connection.set_authorizer(None)
        connection._boundary_owned = False
        if connection.in_transaction:
            connection.rollback()
        raise
    finally:
        connection.close()
