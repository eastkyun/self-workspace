"""Workspace app activation, ordering, settings, and role matrix services."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from access.context import PermissionSnapshot, WorkspaceContext
from access.errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from access.service import AuthorizationService
from identity.database import database_connection
from workspace_platform.settings_schema import SettingsSchemaError, validate_settings


class AppPolicyError(RuntimeError):
    pass


class AppPolicyNotFound(AppPolicyError):
    pass


class AppPolicyForbidden(AppPolicyError):
    pass


class AppPolicyConflict(AppPolicyError):
    pass


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class WorkspaceAppPolicyService:
    def __init__(self, database_path: str | Path, authorization: AuthorizationService) -> None:
        self.database_path = Path(database_path).resolve()
        self.authorization = authorization

    def _manager(self, tx, user_id: str, workspace_id: str) -> tuple[WorkspaceContext, object]:
        try:
            context = self.authorization._resolve_context(tx, user_id, workspace_id)
            snapshot = self.authorization._permission_snapshot(tx, context)
            self.authorization.require(
                context, "workspace.apps.manage", snapshot=snapshot
            )
        except WorkspaceNotAvailable as error:
            raise AppPolicyNotFound("workspace not found") from error
        except PermissionDenied as error:
            raise AppPolicyForbidden("app management forbidden") from error
        return context, snapshot

    def list_apps(
        self, context: WorkspaceContext, *, snapshot: PermissionSnapshot | None = None
    ) -> list[dict[str, object]]:
        try:
            snapshot = snapshot or self.authorization.permission_snapshot(context)
        except WorkspaceNotAvailable as error:
            raise AppPolicyNotFound("workspace not found") from error
        with database_connection(self.database_path) as tx:
            rows = tx.query(
                "SELECT app.key, app.name AS display_name, app.description, app.icon, app.status, "
                "policy.enabled, policy.display_order, policy.settings_json, extension.manifest_json "
                "FROM workspace_apps AS policy JOIN apps AS app ON app.id=policy.app_id "
                "JOIN app_manifests AS extension ON extension.app_id=app.id "
                "WHERE policy.workspace_id=? ORDER BY policy.display_order, app.key",
                (context.workspace_id,),
            )
        return [
            {
                "key": row["key"], "display_name": row["display_name"],
                "description": row["description"], "icon": row["icon"],
                "status": row["status"], "enabled": bool(row["enabled"]),
                "effective": self.authorization.authorize_app(context, row["key"], snapshot=snapshot),
                "display_order": row["display_order"], "settings": json.loads(row["settings_json"]),
                "manifest_checksum": __import__("hashlib").sha256(row["manifest_json"].encode()).hexdigest(),
            }
            for row in rows
        ]

    def require_app(self, context: WorkspaceContext, app_key: str) -> None:
        try:
            self.authorization.require(context, app_key=app_key)
        except (AppNotAvailable, WorkspaceNotAvailable) as error:
            raise AppPolicyNotFound("app not found") from error

    def update_app(
        self,
        user_id: str,
        workspace_id: str,
        app_key: str,
        *,
        enabled: bool | None,
        display_order: int | None,
        settings: dict[str, object] | None,
    ) -> None:
        if display_order is not None and display_order < 0:
            raise ValueError("display_order must be non-negative")
        stamp = _stamp()
        with database_connection(self.database_path) as tx:
            self._manager(tx, user_id, workspace_id)
            row = tx.execute(
                "SELECT app.id, extension.manifest_json FROM apps AS app "
                "JOIN app_manifests AS extension ON extension.app_id=app.id "
                "JOIN workspace_apps AS policy ON policy.app_id=app.id AND policy.workspace_id=? "
                "WHERE app.key=? AND app.status='ACTIVE'",
                (workspace_id, app_key),
            ).fetchone()
            if row is None:
                raise AppPolicyNotFound("app not found")
            if app_key == "home" and enabled is False:
                raise AppPolicyConflict("HOME cannot be disabled")
            if app_key == "workspace" and enabled is True:
                raise AppPolicyConflict("Workspace settings are capability-projected, not an enableable app")
            assignments: list[str] = []
            values: list[object] = []
            if enabled is not None:
                assignments.append("enabled=?"); values.append(int(enabled))
            if display_order is not None:
                assignments.append("display_order=?"); values.append(display_order)
            if settings is not None:
                manifest = json.loads(row["manifest_json"])["platform"]
                try:
                    validate_settings(manifest["settings_schema"], settings)
                except SettingsSchemaError as error:
                    raise ValueError(str(error)) from error
                assignments.append("settings_json=?")
                values.append(json.dumps(settings, sort_keys=True, separators=(",", ":")))
            if assignments:
                assignments.extend(("updated_by=?", "updated_at=?")); values.extend((user_id, stamp))
                values.extend((workspace_id, row["id"]))
                tx.execute(
                    f"UPDATE workspace_apps SET {', '.join(assignments)} WHERE workspace_id=? AND app_id=?",
                    values,
                )
                tx.execute(
                    "INSERT INTO audit_logs VALUES (?, ?, 'workspace.app_changed', 'app', ?, ?, ?)",
                    (workspace_id, user_id, app_key, json.dumps({"fields": [item.partition("=")[0] for item in assignments[:-2]]}, sort_keys=True, separators=(",", ":")), stamp),
                )

    def reorder(self, user_id: str, workspace_id: str, app_keys: tuple[str, ...]) -> None:
        if len(app_keys) != len(set(app_keys)):
            raise ValueError("app order contains duplicates")
        stamp = _stamp()
        with database_connection(self.database_path) as tx:
            self._manager(tx, user_id, workspace_id)
            actual = {
                row["key"]: row["id"]
                for row in tx.query(
                    "SELECT app.key, app.id FROM workspace_apps AS policy JOIN apps AS app ON app.id=policy.app_id WHERE policy.workspace_id=?",
                    (workspace_id,),
                )
            }
            if set(app_keys) != set(actual):
                raise ValueError("app order must contain every workspace app exactly once")
            tx.executemany(
                "UPDATE workspace_apps SET display_order=?, updated_by=?, updated_at=? WHERE workspace_id=? AND app_id=?",
                ((index * 10, user_id, stamp, workspace_id, actual[key]) for index, key in enumerate(app_keys)),
            )
            tx.execute(
                "INSERT INTO audit_logs VALUES (?, ?, 'workspace.apps_reordered', 'workspace', ?, '{}', ?)",
                (workspace_id, user_id, workspace_id, stamp),
            )

    def role_matrix(self, context: WorkspaceContext) -> list[dict[str, object]]:
        try:
            self.authorization.require(context, "workspace.settings.manage")
        except PermissionDenied as error:
            raise AppPolicyForbidden("role matrix forbidden") from error
        with database_connection(self.database_path) as tx:
            roles = tx.query(
                "SELECT id, key, name, description, builtin, system_role FROM roles "
                "WHERE workspace_id=? AND status='ACTIVE' ORDER BY builtin DESC, key",
                (context.workspace_id,),
            )
            permissions = tx.query(
                "SELECT binding.role_id, permission.code FROM role_permissions AS binding "
                "JOIN permissions AS permission ON permission.id=binding.permission_id "
                "JOIN roles AS role ON role.id=binding.role_id WHERE role.workspace_id=? ORDER BY permission.code",
                (context.workspace_id,),
            )
        by_role: dict[str, list[str]] = {}
        for row in permissions:
            by_role.setdefault(row["role_id"], []).append(row["code"])
        return [
            {**dict(role), "permissions": by_role.get(role["id"], [])}
            for role in roles
        ]

    def permission_override_matrix(
        self, context: WorkspaceContext
    ) -> dict[str, object]:
        try:
            self.authorization.require(context, "workspace.permissions.manage")
        except PermissionDenied as error:
            raise AppPolicyForbidden("permission overrides forbidden") from error
        except WorkspaceNotAvailable as error:
            raise AppPolicyNotFound("workspace not found") from error
        with database_connection(self.database_path) as tx:
            members = tx.query(
                """SELECT member.id, member.user_id, account.display_name,
                   account.email_normalized AS email, role.key AS role_key,
                   role.system_role
                FROM workspace_members AS member
                JOIN users AS account ON account.id=member.user_id
                JOIN roles AS role ON role.id=member.role_id
                  AND role.workspace_id=member.workspace_id
                WHERE member.workspace_id=? AND member.status='ACTIVE'
                ORDER BY account.display_name, member.id""",
                (context.workspace_id,),
            )
            permissions = tx.query(
                """SELECT permission.code, permission.name,
                   permission.description, permission.owner_only,
                   app.key AS app_key, app.name AS app_name
                FROM permissions AS permission
                JOIN apps AS app ON app.id=permission.app_id
                WHERE app.status='ACTIVE'
                ORDER BY app.key, permission.code"""
            )
            overrides = tx.query(
                """SELECT override.membership_id, permission.code,
                   override.effect
                FROM member_permission_overrides AS override
                JOIN permissions AS permission
                  ON permission.id=override.permission_id
                WHERE override.workspace_id=?
                ORDER BY override.membership_id, permission.code""",
                (context.workspace_id,),
            )
        return {
            "members": [dict(row) for row in members],
            "permissions": [dict(row) for row in permissions],
            "overrides": [dict(row) for row in overrides],
        }

    def set_permission_override(
        self,
        actor_user_id: str,
        workspace_id: str,
        membership_id: str,
        permission_code: str,
        effect: str,
    ) -> None:
        normalized_effect = effect.strip().upper()
        if normalized_effect not in {"ALLOW", "DENY"}:
            raise ValueError("effect must be ALLOW or DENY")
        stamp = _stamp()
        with database_connection(self.database_path) as tx:
            try:
                actor_context = self.authorization._resolve_context(
                    tx, actor_user_id, workspace_id
                )
            except WorkspaceNotAvailable as error:
                raise AppPolicyNotFound("workspace not found") from error
            snapshot = self.authorization._permission_snapshot(tx, actor_context)
            try:
                self.authorization.require(
                    actor_context,
                    "workspace.permissions.manage",
                    snapshot=snapshot,
                )
            except PermissionDenied as error:
                raise AppPolicyForbidden("permission overrides forbidden") from error
            target = tx.execute(
                """SELECT member.id, role.system_role
                FROM workspace_members AS member
                JOIN roles AS role ON role.id=member.role_id
                  AND role.workspace_id=member.workspace_id
                WHERE member.id=? AND member.workspace_id=?
                  AND member.status='ACTIVE'""",
                (membership_id, workspace_id),
            ).fetchone()
            if target is None:
                raise AppPolicyNotFound("member not found")
            if target["system_role"] == "OWNER":
                raise AppPolicyConflict("Owner permissions cannot be overridden")
            permission = tx.execute(
                """SELECT permission.id, permission.owner_only
                FROM permissions AS permission
                JOIN apps AS app ON app.id=permission.app_id
                WHERE permission.code=? AND app.status='ACTIVE'""",
                (permission_code,),
            ).fetchone()
            if permission is None:
                raise AppPolicyNotFound("permission not found")
            if permission["owner_only"]:
                raise AppPolicyConflict("Owner-only permissions cannot be overridden")
            if not self.authorization.authorize(
                actor_context, permission_code, snapshot=snapshot
            ):
                raise AppPolicyForbidden(
                    "cannot grant or deny a permission the actor does not hold"
                )
            tx.execute(
                """INSERT INTO member_permission_overrides
                (workspace_id, membership_id, permission_id, effect,
                 created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(membership_id, permission_id) DO UPDATE SET
                  effect=excluded.effect, created_by=excluded.created_by,
                  updated_at=excluded.updated_at""",
                (
                    workspace_id, membership_id, permission["id"],
                    normalized_effect, actor_user_id, stamp, stamp,
                ),
            )
            tx.execute(
                "INSERT INTO audit_logs VALUES (?, ?, 'workspace.permission_override_changed', 'membership', ?, ?, ?)",
                (
                    workspace_id,
                    actor_user_id,
                    membership_id,
                    json.dumps(
                        {"permission_code": permission_code, "effect": normalized_effect},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    stamp,
                ),
            )

    def clear_permission_override(
        self,
        actor_user_id: str,
        workspace_id: str,
        membership_id: str,
        permission_code: str,
    ) -> None:
        stamp = _stamp()
        with database_connection(self.database_path) as tx:
            try:
                actor_context = self.authorization._resolve_context(
                    tx, actor_user_id, workspace_id
                )
            except WorkspaceNotAvailable as error:
                raise AppPolicyNotFound("workspace not found") from error
            snapshot = self.authorization._permission_snapshot(tx, actor_context)
            try:
                self.authorization.require(
                    actor_context,
                    "workspace.permissions.manage",
                    snapshot=snapshot,
                )
            except PermissionDenied as error:
                raise AppPolicyForbidden("permission overrides forbidden") from error
            permission = tx.execute(
                "SELECT id, owner_only FROM permissions WHERE code=?",
                (permission_code,),
            ).fetchone()
            if permission is None:
                raise AppPolicyNotFound("permission not found")
            if permission["owner_only"] or not self.authorization.authorize(
                actor_context, permission_code, snapshot=snapshot
            ):
                raise AppPolicyForbidden("permission override removal forbidden")
            deleted = tx.execute(
                """DELETE FROM member_permission_overrides
                WHERE workspace_id=? AND membership_id=? AND permission_id=?""",
                (workspace_id, membership_id, permission["id"]),
            )
            if deleted.rowcount == 0:
                raise AppPolicyNotFound("permission override not found")
            tx.execute(
                "INSERT INTO audit_logs VALUES (?, ?, 'workspace.permission_override_cleared', 'membership', ?, ?, ?)",
                (
                    workspace_id,
                    actor_user_id,
                    membership_id,
                    json.dumps(
                        {"permission_code": permission_code},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    stamp,
                ),
            )
