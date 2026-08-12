"""Single database-backed workspace authorization service."""

from __future__ import annotations

from pathlib import Path

from identity.database import database_connection

from .context import PermissionSnapshot, WorkspaceContext
from .errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from .policy import app_allowed, permission_allowed


class AuthorizationService:
    """Resolve context and policy from Identity DB with no cross-DB access."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()

    @staticmethod
    def _resolve_context(tx, user_id: str, workspace_id: str) -> WorkspaceContext:
        row = tx.execute(
            "SELECT member.id AS membership_id, role.key AS role_key, "
            "COALESCE(version.version, 0) AS authorization_version "
            "FROM workspace_members AS member "
            "JOIN users AS user ON user.id=member.user_id "
            "JOIN workspaces AS workspace ON workspace.id=member.workspace_id "
            "JOIN roles AS role ON role.id=member.role_id AND role.workspace_id=member.workspace_id "
            "LEFT JOIN workspace_authorization_versions AS version ON version.workspace_id=workspace.id "
            "WHERE member.user_id=? AND member.workspace_id=? AND member.status='ACTIVE' "
            "AND user.status='ACTIVE' AND workspace.status='ACTIVE' AND role.status='ACTIVE'",
            (user_id, workspace_id),
        ).fetchone()
        if row is None:
            raise WorkspaceNotAvailable("workspace not available")
        apps = tx.query(
            "SELECT app.id FROM workspace_apps AS policy "
            "JOIN apps AS app ON app.id=policy.app_id "
            "WHERE policy.workspace_id=? AND policy.enabled=1 AND app.status='ACTIVE' "
            "ORDER BY policy.display_order, app.key",
            (workspace_id,),
        )
        return WorkspaceContext(
            user_id=user_id,
            workspace_id=workspace_id,
            membership_id=row["membership_id"],
            role_key=row["role_key"],
            enabled_app_ids=tuple(item["id"] for item in apps),
            authorization_version=int(row["authorization_version"]),
        )

    def resolve_context(self, user_id: str, workspace_id: str) -> WorkspaceContext:
        with database_connection(self.database_path) as tx:
            return self._resolve_context(tx, user_id, workspace_id)

    @staticmethod
    def _permission_snapshot(tx, context: WorkspaceContext) -> PermissionSnapshot:
        current = AuthorizationService._resolve_context(
            tx, context.user_id, context.workspace_id
        )
        if current.membership_id != context.membership_id:
            raise WorkspaceNotAvailable("membership changed")
        enabled_apps = tuple(
            (row["key"], row["id"])
            for row in tx.query(
                "SELECT app.key, app.id FROM workspace_apps AS policy "
                "JOIN apps AS app ON app.id=policy.app_id "
                "WHERE policy.workspace_id=? AND policy.enabled=1 AND app.status='ACTIVE' "
                "ORDER BY policy.display_order, app.key",
                (context.workspace_id,),
            )
        )
        role_permissions = frozenset(
            row["code"]
            for row in tx.query(
                "SELECT permission.code FROM workspace_members AS member "
                "JOIN role_permissions AS binding ON binding.role_id=member.role_id AND binding.effect='ALLOW' "
                "JOIN permissions AS permission ON permission.id=binding.permission_id "
                "WHERE member.id=? AND member.workspace_id=? AND member.status='ACTIVE'",
                (context.membership_id, context.workspace_id),
            )
        )
        override_rows = tx.query(
            "SELECT permission.code, override.effect FROM member_permission_overrides AS override "
            "JOIN permissions AS permission ON permission.id=override.permission_id "
            "WHERE override.membership_id=? AND override.workspace_id=?",
            (context.membership_id, context.workspace_id),
        )
        owner_only = frozenset(
            row["code"] for row in tx.query("SELECT code FROM permissions WHERE owner_only=1")
        )
        read_required_apps = frozenset(
            row["app_key"]
            for row in tx.query(
                "SELECT app.key AS app_key FROM permissions AS permission "
                "JOIN apps AS app ON app.id=permission.app_id "
                "WHERE permission.code=app.key || '.read_all'"
            )
        )
        return PermissionSnapshot(
            workspace_id=context.workspace_id,
            membership_id=context.membership_id,
            role_key=current.role_key,
            version=current.authorization_version,
            enabled_apps=enabled_apps,
            role_permissions=role_permissions,
            explicit_allows=frozenset(row["code"] for row in override_rows if row["effect"] == "ALLOW"),
            explicit_denies=frozenset(row["code"] for row in override_rows if row["effect"] == "DENY"),
            owner_only_permissions=owner_only,
            read_required_apps=read_required_apps,
        )

    def permission_snapshot(self, context: WorkspaceContext) -> PermissionSnapshot:
        with database_connection(self.database_path) as tx:
            return self._permission_snapshot(tx, context)

    @staticmethod
    def _bound_snapshot(context: WorkspaceContext, snapshot: PermissionSnapshot) -> PermissionSnapshot:
        if (
            snapshot.workspace_id != context.workspace_id
            or snapshot.membership_id != context.membership_id
            or snapshot.version != context.authorization_version
            or snapshot.role_key != context.role_key
        ):
            raise WorkspaceNotAvailable("authorization snapshot does not belong to context")
        return snapshot

    def authorize_app(
        self,
        context: WorkspaceContext,
        app_key: str,
        *,
        snapshot: PermissionSnapshot | None = None,
    ) -> bool:
        view = self._bound_snapshot(context, snapshot) if snapshot is not None else self.permission_snapshot(context)
        return app_allowed(view, app_key, user_id=context.user_id)

    def authorize(
        self,
        context: WorkspaceContext,
        permission_code: str,
        resource_owner_id: str | None = None,
        *,
        snapshot: PermissionSnapshot | None = None,
    ) -> bool:
        view = self._bound_snapshot(context, snapshot) if snapshot is not None else self.permission_snapshot(context)
        return permission_allowed(
            view,
            permission_code,
            user_id=context.user_id,
            resource_owner_id=resource_owner_id,
        )

    def require(
        self,
        context: WorkspaceContext,
        permission_code: str | None = None,
        resource_owner_id: str | None = None,
        *,
        app_key: str | None = None,
        snapshot: PermissionSnapshot | None = None,
    ) -> PermissionSnapshot:
        view = self._bound_snapshot(context, snapshot) if snapshot is not None else self.permission_snapshot(context)
        if app_key is not None and not self.authorize_app(context, app_key, snapshot=view):
            raise AppNotAvailable("app not available")
        if permission_code is not None and not self.authorize(
            context, permission_code, resource_owner_id, snapshot=view
        ):
            raise PermissionDenied("permission denied")
        return view
