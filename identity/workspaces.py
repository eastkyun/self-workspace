"""Workspace and membership lifecycle services on the managed Identity boundary."""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from identity.auth import AuthenticatedUser
from access.context import WorkspaceContext
from access.errors import PermissionDenied, WorkspaceNotAvailable
from access.service import AuthorizationService
from identity.database import database_connection
from identity.foundation import (
    BUILTIN_ROLES,
    FOUNDATION_APPS,
    foundation_app_checksum,
    permission_code,
    permission_id,
    role_permission_codes,
)
from identity.notifications import NotificationProvider, WorkspaceInvitationMessage
from identity.primitives import new_uuid, normalize_email, utc_now


logger = logging.getLogger(__name__)


class WorkspaceError(RuntimeError):
    pass


class WorkspaceNotFound(WorkspaceError):
    pass


class WorkspaceForbidden(WorkspaceError):
    pass


class WorkspaceConflict(WorkspaceError):
    pass


@dataclass(frozen=True, slots=True)
class InvitationDelivery:
    invitation_id: str
    recipient: str
    invitation_url: str


@dataclass(frozen=True, slots=True)
class InvitationOperation:
    invitation_id: str
    delivery_status: str


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _token() -> str:
    return secrets.token_urlsafe(32)


def _token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _timezone_name(value: str) -> str:
    candidate = value.strip()
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("workspace timezone must be a valid IANA timezone") from error
    return candidate


def _audit(tx, workspace_id: str, actor_id: str, action: str, target_type: str, target_id: str | None, metadata: dict[str, object], stamp: str) -> None:
    tx.execute(
        "INSERT INTO audit_logs VALUES (?, ?, ?, ?, ?, ?, ?)",
        (workspace_id, actor_id, action, target_type, target_id, json.dumps(metadata, sort_keys=True, separators=(",", ":")), stamp),
    )


class WorkspaceService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        notification_provider: NotificationProvider,
        public_origin: str,
        clock: Callable[[], datetime] = utc_now,
        invitation_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.notification_provider = notification_provider
        self.public_origin = public_origin.rstrip("/")
        self.clock = clock
        self.invitation_ttl = invitation_ttl
        self.authorization = AuthorizationService(self.database_path)

    def resolve_context(self, user_id: str, workspace_id: str) -> WorkspaceContext:
        try:
            return self.authorization.resolve_context(user_id, workspace_id)
        except WorkspaceNotAvailable as error:
            raise WorkspaceNotFound("workspace not found") from error

    def _actor_context(self, tx, user_id: str, workspace_id: str) -> WorkspaceContext:
        """Revalidate mutable authority inside the same write transaction."""
        try:
            return self.authorization._resolve_context(tx, user_id, workspace_id)
        except WorkspaceNotAvailable as error:
            raise WorkspaceNotFound("workspace not found") from error

    def list_workspaces(self, user: AuthenticatedUser) -> list[dict[str, object]]:
        with database_connection(self.database_path) as tx:
            rows = tx.query(
                "SELECT workspace.id, workspace.type, workspace.name, workspace.description, workspace.logo_asset_id, "
                "workspace.timezone, role.key AS role_key, member.id AS membership_id "
                "FROM workspace_members AS member JOIN workspaces AS workspace ON workspace.id=member.workspace_id "
                "JOIN roles AS role ON role.id=member.role_id AND role.workspace_id=member.workspace_id "
                "JOIN users AS user ON user.id=member.user_id "
                "WHERE member.user_id=? AND member.status='ACTIVE' AND workspace.status='ACTIVE' "
                "AND role.status='ACTIVE' AND user.status='ACTIVE' ORDER BY workspace.created_at, workspace.id",
                (user.id,),
            )
        return [dict(row) for row in rows]

    def get_workspace(self, user: AuthenticatedUser, workspace_id: str) -> dict[str, object]:
        with database_connection(self.database_path) as tx:
            context = self._actor_context(tx, user.id, workspace_id)
            row = tx.execute(
                "SELECT id, type, name, description, logo_asset_id, timezone, created_at, updated_at FROM workspaces "
                "WHERE id=? AND status='ACTIVE'", (workspace_id,),
            ).fetchone()
        if row is None:
            raise WorkspaceNotFound("workspace not found")
        result = dict(row)
        result.update({"role_key": context.role_key, "membership_id": context.membership_id})
        return result

    def select_workspace(self, user: AuthenticatedUser, workspace_id: str) -> None:
        with database_connection(self.database_path) as tx:
            self._actor_context(tx, user.id, workspace_id)
            updated = tx.execute(
                "UPDATE sessions SET last_workspace_id=? WHERE id=? AND user_id=? AND revoked_at IS NULL",
                (workspace_id, user.session_id, user.id),
            )
            if updated.rowcount != 1:
                raise WorkspaceNotFound("workspace not found")

    def _seed_workspace(self, tx, workspace_id: str, actor_id: str, membership_id: str, stamp: str) -> None:
        role_ids: dict[str, str] = {}
        for role_key, role_name, system_role in BUILTIN_ROLES:
            role_ids[role_key] = new_uuid()
            tx.execute(
                "INSERT INTO roles VALUES (?, ?, ?, ?, '', 1, ?, 'ACTIVE', ?, ?, ?)",
                (role_ids[role_key], workspace_id, role_key, role_name, system_role, actor_id, stamp, stamp),
            )
        tx.execute(
            "INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
            (membership_id, workspace_id, actor_id, role_ids["owner"], stamp, stamp, stamp),
        )
        for app in FOUNDATION_APPS:
            tx.execute(
                "INSERT OR IGNORE INTO apps VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'ACTIVE', ?, ?, ?)",
                (app.id, app.key, app.name, app.description, app.icon, app.category, app.version, foundation_app_checksum(app), stamp, stamp),
            )
            tx.execute(
                "INSERT INTO workspace_apps VALUES (?, ?, ?, ?, '{}', ?, ?, ?)",
                (workspace_id, app.id, int(app.default_enabled), app.display_order, actor_id, stamp, stamp),
            )
            for permission in app.permissions:
                code = permission_code(app, permission)
                tx.execute(
                    "INSERT OR IGNORE INTO permissions VALUES (?, ?, ?, ?, '', ?, ?, ?)",
                    (permission_id(code), code, app.id, code, int(code in {"workspace.ownership.transfer", "workspace.delete"}), stamp, stamp),
                )
        for role_key, role_id in role_ids.items():
            tx.executemany(
                "INSERT INTO role_permissions VALUES (?, ?, 'ALLOW', ?)",
                ((role_id, permission_id(code), stamp) for code in sorted(role_permission_codes(role_key))),
            )

    def create_team(self, user: AuthenticatedUser, name: str, description: str = "", timezone_name: str = "Asia/Seoul") -> dict[str, object]:
        name = name.strip()
        if not name:
            raise ValueError("workspace name is required")
        timezone_name = _timezone_name(timezone_name)
        stamp = _stamp(self.clock())
        workspace_id, membership_id = new_uuid(), new_uuid()
        with database_connection(self.database_path) as tx:
            active_user = tx.execute("SELECT 1 FROM users WHERE id=? AND status='ACTIVE'", (user.id,)).fetchone()
            if active_user is None:
                raise WorkspaceNotFound("user not found")
            tx.execute(
                "INSERT INTO workspaces VALUES (?, 'TEAM', ?, ?, NULL, ?, 'ACTIVE', ?, ?, ?, NULL)",
                (workspace_id, name, description.strip(), timezone_name, user.id, stamp, stamp),
            )
            self._seed_workspace(tx, workspace_id, user.id, membership_id, stamp)
            _audit(tx, workspace_id, user.id, "workspace.created", "workspace", workspace_id, {"type": "TEAM"}, stamp)
        return self.get_workspace(user, workspace_id)

    def _require_permission(self, tx, context: WorkspaceContext, permission_code: str) -> None:
        try:
            snapshot = self.authorization._permission_snapshot(tx, context)
            self.authorization.require(context, permission_code, snapshot=snapshot)
        except PermissionDenied as error:
            raise WorkspaceForbidden("workspace management forbidden") from error

    def update_workspace(self, user: AuthenticatedUser, workspace_id: str, *, name: str | None, description: str | None, timezone_name: str | None) -> dict[str, object]:
        changes: list[str] = []
        values: list[object] = []
        for column, value in (("name", name.strip() if name is not None else None), ("description", description.strip() if description is not None else None), ("timezone", _timezone_name(timezone_name) if timezone_name is not None else None)):
            if value is not None:
                if column == "name" and not value:
                    raise ValueError("workspace name is required")
                changes.append(f"{column}=?")
                values.append(value)
        stamp = _stamp(self.clock())
        with database_connection(self.database_path) as tx:
            context = self._actor_context(tx, user.id, workspace_id)
            self._require_permission(tx, context, "workspace.settings.manage")
            if changes:
                values.extend((stamp, workspace_id))
                tx.execute(f"UPDATE workspaces SET {', '.join(changes)}, updated_at=? WHERE id=? AND status='ACTIVE'", values)
                _audit(tx, workspace_id, user.id, "workspace.updated", "workspace", workspace_id, {"fields": [item.split('=')[0] for item in changes]}, stamp)
        return self.get_workspace(user, workspace_id)

    def delete_workspace(self, user: AuthenticatedUser, workspace_id: str) -> None:
        stamp = _stamp(self.clock())
        with database_connection(self.database_path) as tx:
            context = self._actor_context(tx, user.id, workspace_id)
            self._require_permission(tx, context, "workspace.delete")
            tx.execute("UPDATE workspace_invitations SET status='CANCELED', updated_at=? WHERE workspace_id=? AND status='PENDING'", (stamp, workspace_id))
            tx.execute("UPDATE workspaces SET status='DELETED', deleted_at=?, updated_at=? WHERE id=? AND status='ACTIVE'", (stamp, stamp, workspace_id))
            _audit(tx, workspace_id, user.id, "workspace.deleted", "workspace", workspace_id, {}, stamp)

    def list_members(self, user: AuthenticatedUser, workspace_id: str) -> list[dict[str, object]]:
        with database_connection(self.database_path) as tx:
            self._actor_context(tx, user.id, workspace_id)
            rows = tx.query(
                "SELECT member.id, member.user_id, account.email_normalized AS email, account.display_name, role.key AS role_key, member.joined_at "
                "FROM workspace_members AS member JOIN users AS account ON account.id=member.user_id "
                "JOIN roles AS role ON role.id=member.role_id AND role.workspace_id=member.workspace_id "
                "WHERE member.workspace_id=? AND member.status='ACTIVE' ORDER BY member.joined_at, member.id",
                (workspace_id,),
            )
        return [dict(row) for row in rows]

    def _invitation_delivery(self, invitation_id: str, recipient: str, token: str) -> InvitationDelivery:
        url = f"{self.public_origin}/invitations/{quote(token, safe='')}"
        return InvitationDelivery(invitation_id, recipient, url)

    def _send_invitation(self, delivery: InvitationDelivery) -> str:
        try:
            self.notification_provider.send_workspace_invitation(
                WorkspaceInvitationMessage(delivery.recipient, delivery.invitation_url)
            )
            return "SENT"
        except Exception:
            # Delivery is outside the Identity transaction. Preserve the
            # recoverable invitation ID while recording a secret-free failure;
            # managers can resend without creating an unknown duplicate.
            logger.warning("workspace invitation delivery failed", extra={"security_event": "workspace.invitation_delivery_failed"})
            stamp = _stamp(self.clock())
            with database_connection(self.database_path) as tx:
                row = tx.execute(
                    "SELECT workspace_id, invited_by FROM workspace_invitations WHERE id=?",
                    (delivery.invitation_id,),
                ).fetchone()
                if row is not None:
                    _audit(tx, row["workspace_id"], row["invited_by"], "invitation.delivery_failed", "invitation", delivery.invitation_id, {}, stamp)
            return "FAILED"

    def invite(self, user: AuthenticatedUser, workspace_id: str, email: str, role_key: str) -> InvitationOperation:
        role_key = role_key.strip().lower()
        recipient = normalize_email(email)
        now = self.clock(); stamp = _stamp(now)
        token, invitation_id = _token(), new_uuid()
        try:
            with database_connection(self.database_path) as tx:
                context = self._actor_context(tx, user.id, workspace_id)
                self._require_permission(tx, context, "workspace.members.invite")
                if role_key == "owner" and context.role_key != "owner":
                    raise WorkspaceForbidden("owner role assignment forbidden")
                workspace = tx.execute("SELECT type FROM workspaces WHERE id=? AND status='ACTIVE'", (workspace_id,)).fetchone()
                if workspace is None:
                    raise WorkspaceNotFound("workspace not found")
                if workspace["type"] == "PERSONAL":
                    raise WorkspaceConflict("personal workspace cannot invite members")
                role = tx.execute("SELECT 1 FROM roles WHERE workspace_id=? AND key=? AND status='ACTIVE'", (workspace_id, role_key)).fetchone()
                if role is None:
                    raise ValueError("unknown role")
                active = tx.execute("SELECT 1 FROM workspace_members AS member JOIN users AS account ON account.id=member.user_id WHERE member.workspace_id=? AND account.email_normalized=? AND member.status='ACTIVE'", (workspace_id, recipient)).fetchone()
                if active is not None:
                    raise WorkspaceConflict("user is already a member")
                tx.execute(
                    "INSERT INTO workspace_invitations VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, ?, ?)",
                    (invitation_id, workspace_id, recipient, role_key, _token_hash(token), _stamp(now + self.invitation_ttl), user.id, stamp, stamp),
                )
                _audit(tx, workspace_id, user.id, "invitation.created", "invitation", invitation_id, {"role_key": role_key}, stamp)
        except sqlite3.IntegrityError as error:
            raise WorkspaceConflict("pending invitation already exists") from error
        delivery = self._invitation_delivery(invitation_id, recipient, token)
        return InvitationOperation(invitation_id, self._send_invitation(delivery))

    def inspect_invitation(self, user: AuthenticatedUser, token: str) -> dict[str, object]:
        now = self.clock(); stamp = _stamp(now)
        conflict: str | None = None
        result: dict[str, object] | None = None
        with database_connection(self.database_path) as tx:
            row = tx.execute(
                "SELECT invitation.id, invitation.workspace_id, invitation.email_normalized, invitation.role_key, invitation.status, invitation.expires_at, workspace.name "
                "FROM workspace_invitations AS invitation JOIN workspaces AS workspace ON workspace.id=invitation.workspace_id "
                "JOIN users AS account ON account.id=? AND account.status='ACTIVE' "
                "WHERE invitation.token_hash=? AND workspace.status='ACTIVE'", (user.id, _token_hash(token)),
            ).fetchone()
            if row is None or row["email_normalized"] != user.email:
                raise WorkspaceNotFound("invitation not found")
            if row["status"] == "PENDING" and now >= _parse(row["expires_at"]):
                tx.execute("UPDATE workspace_invitations SET status='EXPIRED', updated_at=? WHERE id=? AND status='PENDING'", (stamp, row["id"]))
                conflict = "invitation expired"
            elif row["status"] != "PENDING":
                conflict = "invitation is no longer active"
            else:
                result = dict(row)
        if conflict:
            raise WorkspaceConflict(conflict)
        return result or {}

    def _consume(self, user: AuthenticatedUser, token: str, final_status: str) -> dict[str, object] | None:
        now = self.clock(); stamp = _stamp(now)
        conflict: str | None = None
        result: dict[str, object] | None = None
        with database_connection(self.database_path) as tx:
            row = tx.execute(
                "SELECT invitation.*, workspace.status AS workspace_status FROM workspace_invitations AS invitation "
                "JOIN workspaces AS workspace ON workspace.id=invitation.workspace_id "
                "JOIN users AS account ON account.id=? AND account.status='ACTIVE' WHERE invitation.token_hash=?",
                (user.id, _token_hash(token)),
            ).fetchone()
            if row is None or row["email_normalized"] != user.email or row["workspace_status"] != "ACTIVE":
                raise WorkspaceNotFound("invitation not found")
            if row["status"] != "PENDING":
                raise WorkspaceConflict("invitation is no longer active")
            if now >= _parse(row["expires_at"]):
                tx.execute("UPDATE workspace_invitations SET status='EXPIRED', updated_at=? WHERE id=? AND status='PENDING'", (stamp, row["id"]))
                conflict = "invitation expired"
            elif final_status == "ACCEPTED":
                active = tx.execute("SELECT 1 FROM workspace_members WHERE workspace_id=? AND user_id=? AND status='ACTIVE'", (row["workspace_id"], user.id)).fetchone()
                if active is not None:
                    tx.execute("UPDATE workspace_invitations SET status='CANCELED', updated_at=? WHERE id=? AND status='PENDING'", (stamp, row["id"]))
                    conflict = "user is already a member"
                else:
                    role = tx.execute("SELECT id FROM roles WHERE workspace_id=? AND key=? AND status='ACTIVE'", (row["workspace_id"], row["role_key"])).fetchone()
                    if role is None:
                        tx.execute("UPDATE workspace_invitations SET status='CANCELED', updated_at=? WHERE id=? AND status='PENDING'", (stamp, row["id"]))
                        conflict = "invitation role unavailable"
                    else:
                        membership_id = new_uuid()
                        tx.execute("UPDATE workspace_invitations SET status='ACCEPTED', accepted_by=?, updated_at=? WHERE id=? AND status='PENDING'", (user.id, stamp, row["id"]))
                        tx.execute("INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)", (membership_id, row["workspace_id"], user.id, role["id"], stamp, stamp, stamp))
                        _audit(tx, row["workspace_id"], user.id, "invitation.accepted", "invitation", row["id"], {"membership_id": membership_id}, stamp)
                        result = {"workspace_id": row["workspace_id"], "membership_id": membership_id}
            else:
                tx.execute("UPDATE workspace_invitations SET status=?, updated_at=? WHERE id=? AND status='PENDING'", (final_status, stamp, row["id"]))
                _audit(tx, row["workspace_id"], user.id, "invitation.rejected", "invitation", row["id"], {}, stamp)
        if conflict:
            raise WorkspaceConflict(conflict)
        return result

    def accept_invitation(self, user: AuthenticatedUser, token: str) -> dict[str, object]:
        return self._consume(user, token, "ACCEPTED") or {}

    def reject_invitation(self, user: AuthenticatedUser, token: str) -> None:
        self._consume(user, token, "REJECTED")

    def cancel_invitation(self, user: AuthenticatedUser, workspace_id: str, invitation_id: str) -> None:
        stamp = _stamp(self.clock())
        with database_connection(self.database_path) as tx:
            context = self._actor_context(tx, user.id, workspace_id); self._require_permission(tx, context, "workspace.members.invite")
            row = tx.execute("SELECT status FROM workspace_invitations WHERE id=? AND workspace_id=?", (invitation_id, workspace_id)).fetchone()
            if row is None:
                raise WorkspaceNotFound("invitation not found")
            if row["status"] != "PENDING":
                raise WorkspaceConflict("invitation is no longer active")
            tx.execute("UPDATE workspace_invitations SET status='CANCELED', updated_at=? WHERE id=? AND status='PENDING'", (stamp, invitation_id))
            _audit(tx, workspace_id, user.id, "invitation.canceled", "invitation", invitation_id, {}, stamp)

    def resend_invitation(self, user: AuthenticatedUser, workspace_id: str, invitation_id: str) -> InvitationOperation:
        now = self.clock(); stamp = _stamp(now); token, replacement_id = _token(), new_uuid()
        with database_connection(self.database_path) as tx:
            context = self._actor_context(tx, user.id, workspace_id); self._require_permission(tx, context, "workspace.members.invite")
            old = tx.execute("SELECT email_normalized, role_key, status FROM workspace_invitations WHERE id=? AND workspace_id=?", (invitation_id, workspace_id)).fetchone()
            if old is None:
                raise WorkspaceNotFound("invitation not found")
            if old["status"] != "PENDING":
                raise WorkspaceConflict("invitation is no longer active")
            tx.execute("UPDATE workspace_invitations SET status='CANCELED', updated_at=? WHERE id=? AND status='PENDING'", (stamp, invitation_id))
            tx.execute("INSERT INTO workspace_invitations VALUES (?, ?, ?, ?, ?, 'PENDING', ?, ?, NULL, ?, ?)", (replacement_id, workspace_id, old["email_normalized"], old["role_key"], _token_hash(token), _stamp(now + self.invitation_ttl), user.id, stamp, stamp))
            _audit(tx, workspace_id, user.id, "invitation.resent", "invitation", replacement_id, {"replaces": invitation_id}, stamp)
        delivery = self._invitation_delivery(replacement_id, old["email_normalized"], token)
        return InvitationOperation(replacement_id, self._send_invitation(delivery))

    def change_member_role(self, user: AuthenticatedUser, workspace_id: str, member_id: str, role_key: str) -> None:
        role_key = role_key.strip().lower(); stamp = _stamp(self.clock())
        with database_connection(self.database_path) as tx:
            context = self._actor_context(tx, user.id, workspace_id); self._require_permission(tx, context, "workspace.roles.assign")
            target = tx.execute("SELECT role.system_role FROM workspace_members AS member JOIN roles AS role ON role.id=member.role_id WHERE member.id=? AND member.workspace_id=? AND member.status='ACTIVE'", (member_id, workspace_id)).fetchone()
            role = tx.execute("SELECT id, system_role FROM roles WHERE workspace_id=? AND key=? AND status='ACTIVE'", (workspace_id, role_key)).fetchone()
            if target is None:
                raise WorkspaceNotFound("member not found")
            if role is None:
                raise ValueError("unknown role")
            if context.role_key == "admin" and (target["system_role"] == "OWNER" or role["system_role"] == "OWNER"):
                raise WorkspaceForbidden("admin cannot change owner membership")
            try:
                tx.execute("UPDATE workspace_members SET role_id=?, updated_at=? WHERE id=? AND workspace_id=? AND status='ACTIVE'", (role["id"], stamp, member_id, workspace_id))
            except sqlite3.IntegrityError as error:
                raise WorkspaceConflict("active workspace must retain an owner") from error
            _audit(tx, workspace_id, user.id, "member.role_changed", "membership", member_id, {"role_key": role_key}, stamp)

    def remove_member(self, user: AuthenticatedUser, workspace_id: str, member_id: str) -> None:
        stamp = _stamp(self.clock())
        with database_connection(self.database_path) as tx:
            context = self._actor_context(tx, user.id, workspace_id); self._require_permission(tx, context, "workspace.members.remove")
            target = tx.execute("SELECT role.system_role FROM workspace_members AS member JOIN roles AS role ON role.id=member.role_id WHERE member.id=? AND member.workspace_id=? AND member.status='ACTIVE'", (member_id, workspace_id)).fetchone()
            if target is None:
                raise WorkspaceNotFound("member not found")
            if context.role_key == "admin" and target["system_role"] == "OWNER":
                raise WorkspaceForbidden("admin cannot remove owner")
            try:
                tx.execute("UPDATE workspace_members SET status='LEFT', updated_at=? WHERE id=? AND workspace_id=? AND status='ACTIVE'", (stamp, member_id, workspace_id))
            except sqlite3.IntegrityError as error:
                raise WorkspaceConflict("active workspace must retain an owner") from error
            _audit(tx, workspace_id, user.id, "member.removed", "membership", member_id, {}, stamp)

    def leave_workspace(self, user: AuthenticatedUser, workspace_id: str) -> None:
        stamp = _stamp(self.clock())
        with database_connection(self.database_path) as tx:
            context = self._actor_context(tx, user.id, workspace_id)
            workspace = tx.execute("SELECT type FROM workspaces WHERE id=? AND status='ACTIVE'", (workspace_id,)).fetchone()
            if workspace is None:
                raise WorkspaceNotFound("workspace not found")
            if workspace["type"] == "PERSONAL":
                raise WorkspaceConflict("personal workspace cannot be left")
            try:
                tx.execute("UPDATE workspace_members SET status='LEFT', updated_at=? WHERE id=? AND status='ACTIVE'", (stamp, context.membership_id))
            except sqlite3.IntegrityError as error:
                raise WorkspaceConflict("active workspace must retain an owner") from error
            _audit(tx, workspace_id, user.id, "member.left", "membership", context.membership_id, {}, stamp)

    def transfer_ownership(self, user: AuthenticatedUser, workspace_id: str, target_member_id: str) -> None:
        stamp = _stamp(self.clock())
        with database_connection(self.database_path) as tx:
            context = self._actor_context(tx, user.id, workspace_id); self._require_permission(tx, context, "workspace.ownership.transfer")
            workspace = tx.execute("SELECT type FROM workspaces WHERE id=? AND status='ACTIVE'", (workspace_id,)).fetchone()
            target = tx.execute("SELECT id FROM workspace_members WHERE id=? AND workspace_id=? AND status='ACTIVE'", (target_member_id, workspace_id)).fetchone()
            if workspace is None or target is None:
                raise WorkspaceNotFound("member not found")
            if workspace["type"] != "TEAM":
                raise WorkspaceConflict("personal workspace ownership cannot be transferred")
            owner_role = tx.execute("SELECT id FROM roles WHERE workspace_id=? AND key='owner' AND status='ACTIVE'", (workspace_id,)).fetchone()
            admin_role = tx.execute("SELECT id FROM roles WHERE workspace_id=? AND key='admin' AND status='ACTIVE'", (workspace_id,)).fetchone()
            if target_member_id == context.membership_id:
                raise WorkspaceConflict("target is already the requesting owner")
            try:
                tx.execute("UPDATE workspace_members SET role_id=?, updated_at=? WHERE id=? AND workspace_id=?", (owner_role["id"], stamp, target_member_id, workspace_id))
                tx.execute("UPDATE workspace_members SET role_id=?, updated_at=? WHERE id=? AND workspace_id=?", (admin_role["id"], stamp, context.membership_id, workspace_id))
            except sqlite3.IntegrityError as error:
                raise WorkspaceConflict("ownership transfer is not valid") from error
            _audit(tx, workspace_id, user.id, "workspace.ownership_transferred", "membership", target_member_id, {"previous_owner_membership_id": context.membership_id}, stamp)
