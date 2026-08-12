"""Permission-aware workspace activity, favorites, and recent views."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from access.context import WorkspaceContext
from access.errors import WorkspaceNotAvailable
from access.service import AuthorizationService
from identity.database import database_connection
from workspace_platform.user_state_resources import WorkspaceUserStateResourceResolver


class UserStateError(RuntimeError): pass
class UserStateNotFound(UserStateError): pass
class UserStateForbidden(UserStateError): pass


_RESOURCE_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="microseconds").replace("+00:00", "Z")


class WorkspaceUserStateService:
    def __init__(
        self, database_path: str | Path, authorization: AuthorizationService,
        resources: WorkspaceUserStateResourceResolver | None = None,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.authorization = authorization
        self.resources = resources

    @staticmethod
    def _validate_resource(resource_type: str, resource_id: str) -> None:
        if not _RESOURCE_TYPE.fullmatch(resource_type):
            raise ValueError("invalid resource type")
        if not resource_id.strip() or len(resource_id) > 256:
            raise ValueError("invalid resource id")

    def _app(self, tx, context: WorkspaceContext, app_key: str):
        try:
            snapshot = self.authorization._permission_snapshot(tx, context)
        except WorkspaceNotAvailable as error:
            raise UserStateNotFound("workspace not found") from error
        if not self.authorization.authorize_app(context, app_key, snapshot=snapshot):
            raise UserStateNotFound("app not found")
        row = tx.execute("SELECT id FROM apps WHERE key=? AND status='ACTIVE'", (app_key,)).fetchone()
        if row is None:
            raise UserStateNotFound("app not found")
        return row["id"]

    def _readable_apps(self, context: WorkspaceContext, snapshot) -> tuple[tuple[str, str], ...]:
        readable = []
        for app_key, app_id in snapshot.enabled_apps:
            if not self.authorization.authorize_app(context, app_key, snapshot=snapshot):
                continue
            permission = {"notes": "notes.read_all", "calendar": "calendar.read_all"}.get(app_key)
            if permission and self.authorization.authorize(context, permission, snapshot=snapshot):
                readable.append((app_key, app_id))
        return tuple(readable)

    def _resolve_write(self, context: WorkspaceContext, resource_type: str, resource_id: str, app_key: str):
        self._validate_resource(resource_type, resource_id)
        if self.resources is None:
            raise UserStateNotFound("resource resolver unavailable")
        resolved = self.resources.resolve_one(context, resource_type, resource_id)
        if resolved.status != "visible" or resolved.link is None or resolved.app_key != app_key:
            raise UserStateNotFound("resource not found")
        return resolved.link

    def _list_resources(self, context: WorkspaceContext, table: str, timestamp: str, limit: int) -> list[dict[str, object]]:
        cap = min(max(limit, 1), 100)
        with database_connection(self.database_path) as tx:
            snapshot = self.authorization._permission_snapshot(tx, context)
        readable_ids = tuple(app_id for _, app_id in self._readable_apps(context, snapshot))
        if not readable_ids or self.resources is None:
            return []
        result: list[dict[str, object]] = []
        missing: list[tuple[str, str]] = []
        refreshed: list[tuple[str, str, str, str]] = []
        offset = 0
        placeholders = ",".join("?" for _ in readable_ids)
        while len(result) < cap:
            with database_connection(self.database_path) as tx:
                rows = tx.query(
                    f"SELECT state.resource_type,state.resource_id,state.app_id,app.key AS app_key,"
                    f"state.label,state.href,state.{timestamp} FROM {table} AS state "
                    "JOIN apps AS app ON app.id=state.app_id WHERE state.workspace_id=? AND state.user_id=? "
                    f"AND state.app_id IN ({placeholders}) ORDER BY state.{timestamp} DESC,"
                    "state.resource_type ASC,state.resource_id ASC LIMIT 100 OFFSET ?",
                    (context.workspace_id, context.user_id, *readable_ids, offset),
                )
            if not rows:
                break
            offset += len(rows)
            grouped: dict[str, list[str]] = defaultdict(list)
            for row in rows:
                grouped[row["resource_type"]].append(row["resource_id"])
            resolutions = self.resources.resolve_many(
                context, {key: tuple(values) for key, values in grouped.items()}
            )
            for row in rows:
                resolution = resolutions[(row["resource_type"], row["resource_id"])]
                if resolution.status == "missing":
                    missing.append((row["resource_type"], row["resource_id"]))
                    continue
                if resolution.status != "visible" or resolution.link is None:
                    continue
                link = resolution.link
                refreshed.append((link.label, link.href, row["resource_type"], row["resource_id"]))
                result.append({
                    "resource_type": row["resource_type"], "resource_id": row["resource_id"],
                    "app_key": row["app_key"], "label": link.label, "href": link.href,
                    timestamp: row[timestamp],
                })
                if len(result) >= cap:
                    break
        if missing or refreshed:
            with database_connection(self.database_path) as tx:
                for resource_type, resource_id in missing:
                    tx.execute(
                        f"DELETE FROM {table} WHERE workspace_id=? AND user_id=? AND resource_type=? AND resource_id=?",
                        (context.workspace_id, context.user_id, resource_type, resource_id),
                    )
                for label, href, resource_type, resource_id in refreshed:
                    tx.execute(
                        f"UPDATE {table} SET label=?,href=? WHERE workspace_id=? AND user_id=? AND resource_type=? AND resource_id=?",
                        (label, href, context.workspace_id, context.user_id, resource_type, resource_id),
                    )
        return result

    def list_favorites(self, context: WorkspaceContext, limit: int = 20) -> list[dict[str, object]]:
        return self._list_resources(context, "workspace_favorites", "updated_at", limit)

    def list_recent(self, context: WorkspaceContext, limit: int = 20) -> list[dict[str, object]]:
        return self._list_resources(context, "workspace_recent_views", "viewed_at", limit)

    def set_favorite(self, context: WorkspaceContext, resource_type: str, resource_id: str, *, app_key: str) -> None:
        link = self._resolve_write(context, resource_type, resource_id, app_key)
        stamp = _stamp()
        with database_connection(self.database_path) as tx:
            app_id = self._app(tx, context, app_key)
            tx.execute(
                "INSERT INTO workspace_favorites VALUES (?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(workspace_id,user_id,resource_type,resource_id) DO UPDATE SET "
                "app_id=excluded.app_id,label=excluded.label,href=excluded.href,updated_at=excluded.updated_at",
                (context.workspace_id, context.user_id, app_id, resource_type, resource_id,
                 link.label, link.href, stamp, stamp),
            )

    def clear_favorite(self, context: WorkspaceContext, resource_type: str, resource_id: str) -> None:
        self._validate_resource(resource_type, resource_id)
        with database_connection(self.database_path) as tx:
            self.authorization._permission_snapshot(tx, context)
            deleted = tx.execute(
                "DELETE FROM workspace_favorites WHERE workspace_id=? AND user_id=? AND resource_type=? AND resource_id=?",
                (context.workspace_id, context.user_id, resource_type, resource_id),
            )
            if deleted.rowcount == 0:
                raise UserStateNotFound("favorite not found")

    def record_recent(self, context: WorkspaceContext, resource_type: str, resource_id: str, *, app_key: str) -> None:
        link = self._resolve_write(context, resource_type, resource_id, app_key)
        now = _now()
        with database_connection(self.database_path) as tx:
            app_id = self._app(tx, context, app_key)
            existing = tx.execute(
                "SELECT viewed_at FROM workspace_recent_views WHERE workspace_id=? AND user_id=? AND resource_type=? AND resource_id=?",
                (context.workspace_id, context.user_id, resource_type, resource_id),
            ).fetchone()
            if existing is not None:
                viewed = datetime.fromisoformat(existing["viewed_at"].replace("Z", "+00:00"))
                if now - viewed < timedelta(seconds=30):
                    return
            tx.execute(
                "INSERT INTO workspace_recent_views VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(workspace_id,user_id,resource_type,resource_id) DO UPDATE SET "
                "app_id=excluded.app_id,label=excluded.label,href=excluded.href,viewed_at=excluded.viewed_at",
                (context.workspace_id, context.user_id, app_id, resource_type, resource_id,
                 link.label, link.href, _stamp(now)),
            )
            tx.execute(
                "DELETE FROM workspace_recent_views WHERE rowid IN (SELECT rowid FROM workspace_recent_views "
                "WHERE workspace_id=? AND user_id=? ORDER BY viewed_at DESC,resource_type ASC,resource_id ASC LIMIT -1 OFFSET 100)",
                (context.workspace_id, context.user_id),
            )

    def list_activity(self, context: WorkspaceContext, limit: int = 20) -> list[dict[str, object]]:
        cap = min(max(limit, 1), 100)
        with database_connection(self.database_path) as tx:
            snapshot = self.authorization._permission_snapshot(tx, context)
            readable_keys = tuple(key for key, _ in self._readable_apps(context, snapshot))
            if not readable_keys:
                return []
            placeholders = ",".join("?" for _ in readable_keys)
            rows = tx.query(
                "SELECT activity.id,activity.action,activity.resource_type AS target_type,"
                "activity.resource_id AS target_id,activity.app_key,"
                "activity.actor_user_id,activity.created_at,account.display_name AS actor_name "
                "FROM activity_logs AS activity LEFT JOIN users AS account ON account.id=activity.actor_user_id "
                f"WHERE activity.workspace_id=? AND activity.app_key IN ({placeholders}) "
                "ORDER BY activity.created_at DESC,activity.id DESC LIMIT ?",
                (context.workspace_id, *readable_keys, cap),
            )
        grouped: dict[str, list[str]] = defaultdict(list)
        for row in rows:
            grouped[row["target_type"]].append(row["target_id"])
        resolutions = self.resources.resolve_many(
            context, {key: tuple(values) for key, values in grouped.items()}
        ) if self.resources is not None and grouped else {}
        result = []
        for row in rows:
            resolution = resolutions.get((row["target_type"], row["target_id"]))
            if resolution is not None and resolution.status in {"hidden", "unavailable"}:
                continue
            link = resolution.link if resolution is not None and resolution.status == "visible" else None
            result.append({
                "action": row["action"], "target_type": row["target_type"],
                "target_id": row["target_id"], "label": link.label if link else row["target_type"],
                "href": link.href if link else None, "actor_name": row["actor_name"],
                "app_key": row["app_key"], "created_at": row["created_at"],
            })
        return result
