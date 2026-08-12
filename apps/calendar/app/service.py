from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from access.context import WorkspaceContext
from access.errors import PermissionDenied
from access.service import AuthorizationService
from apps.calendar.app import repository
from shared.activity import ActivitySink, NullActivitySink


class TaskNotFoundError(LookupError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class CalendarService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        authorization: AuthorizationService,
        clock: Callable[[], datetime] = utc_now,
        activity_sink: ActivitySink | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.authorization = authorization
        self.clock = clock
        self.activity_sink = activity_sink or NullActivitySink()

    def _activity(self, context: WorkspaceContext, action: str, task: dict[str, Any]) -> None:
        self.activity_sink.record(
            context, app_key="calendar", action=action, resource_type="calendar",
            resource_id=str(task["id"]),
            metadata={"status": str(task.get("status", ""))},
        )

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _timestamp(self) -> str:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Calendar clock must return a timezone-aware datetime")
        return value.astimezone(UTC).isoformat(timespec="seconds")

    def _require_read(self, context: WorkspaceContext):
        return self.authorization.require(
            context, "calendar.read_all", app_key="calendar"
        )

    def _require_resource_action(
        self, context: WorkspaceContext, action: str, owner_id: str
    ) -> None:
        snapshot = self._require_read(context)
        if self.authorization.authorize(
            context, f"calendar.{action}_all", snapshot=snapshot
        ):
            return
        if self.authorization.authorize(
            context,
            f"calendar.{action}_own",
            owner_id,
            snapshot=snapshot,
        ):
            return
        raise PermissionDenied("permission denied")

    @staticmethod
    def _with_topics(connection, workspace_id: str, task: dict[str, Any]):
        return {
            **task,
            "topics": repository.list_task_topics(
                connection, workspace_id, int(task["id"])
            ),
        }

    def list_tasks(self, context: WorkspaceContext) -> list[dict[str, Any]]:
        self._require_read(context)
        with self._connection() as connection:
            return [
                self._with_topics(connection, context.workspace_id, task)
                for task in repository.list_tasks(connection, context.workspace_id)
            ]

    def get_task(self, context: WorkspaceContext, task_id: int) -> dict[str, Any]:
        self._require_read(context)
        with self._connection() as connection:
            task = repository.get_task(connection, context.workspace_id, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            return self._with_topics(connection, context.workspace_id, task)

    def create_task(
        self,
        context: WorkspaceContext,
        *,
        title: str,
        description: str,
        status: str,
        all_day: bool,
        start_at: str,
        due_at: str,
        topic_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        snapshot = self._require_read(context)
        if not self.authorization.authorize(
            context, "calendar.create", snapshot=snapshot
        ):
            raise PermissionDenied("permission denied")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = repository.create_task(
                connection,
                context.workspace_id,
                actor_id=context.user_id,
                title=title,
                description=description,
                status=status,
                position=repository.next_position(
                    connection, context.workspace_id, status
                ),
                all_day=all_day,
                start_at=start_at,
                due_at=due_at,
                timestamp=self._timestamp(),
            )
            if not repository.replace_task_topics(
                connection, context.workspace_id, int(task["id"]), topic_ids or []
            ):
                raise ValueError("unknown topic IDs")
            created = self._with_topics(connection, context.workspace_id, task)
        self._activity(context, "calendar.created", created)
        return created

    def update_task(
        self,
        context: WorkspaceContext,
        task_id: int,
        *,
        title: str,
        description: str,
        status: str,
        position: int,
        all_day: bool,
        start_at: str,
        due_at: str,
        topic_ids: list[int] | None = None,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = repository.get_task(connection, context.workspace_id, task_id)
            if current is None:
                raise TaskNotFoundError(task_id)
            self._require_resource_action(
                context, "update", str(current["created_by"])
            )
            old_status = str(current["status"])
            target_ids = repository.ordered_task_ids(
                connection, context.workspace_id, status, excluded_id=task_id
            )
            target_position = min(position, len(target_ids))
            target_ids.insert(target_position, task_id)
            if old_status != status:
                repository.write_positions(
                    connection,
                    context.workspace_id,
                    old_status,
                    repository.ordered_task_ids(
                        connection,
                        context.workspace_id,
                        old_status,
                        excluded_id=task_id,
                    ),
                )
            repository.write_positions(
                connection, context.workspace_id, status, target_ids
            )
            updated = repository.update_task(
                connection,
                context.workspace_id,
                task_id,
                actor_id=context.user_id,
                title=title,
                description=description,
                status=status,
                position=target_position,
                all_day=all_day,
                start_at=start_at,
                due_at=due_at,
                timestamp=self._timestamp(),
            )
            assert updated is not None
            if topic_ids is not None and not repository.replace_task_topics(
                connection, context.workspace_id, task_id, topic_ids
            ):
                raise ValueError("unknown topic IDs")
            result = self._with_topics(connection, context.workspace_id, updated)
        self._activity(context, "calendar.updated", result)
        return result

    def delete_task(self, context: WorkspaceContext, task_id: int) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = repository.get_task(connection, context.workspace_id, task_id)
            if task is None:
                raise TaskNotFoundError(task_id)
            self._require_resource_action(
                context, "delete", str(task["created_by"])
            )
            if not repository.soft_delete_task(
                connection,
                context.workspace_id,
                task_id,
                actor_id=context.user_id,
                timestamp=self._timestamp(),
            ):
                raise TaskNotFoundError(task_id)
            repository.write_positions(
                connection,
                context.workspace_id,
                str(task["status"]),
                repository.ordered_task_ids(
                    connection,
                    context.workspace_id,
                    str(task["status"]),
                    excluded_id=task_id,
                ),
            )
        self._activity(context, "calendar.deleted", task)

    def restore_task(self, context: WorkspaceContext, task_id: int) -> dict[str, Any]:
        snapshot = self._require_read(context)
        if not self.authorization.authorize(
            context, "calendar.manage", snapshot=snapshot
        ):
            raise PermissionDenied("permission denied")
        with self._connection() as connection:
            deleted = repository.get_deleted_task(
                connection, context.workspace_id, task_id
            )
            if deleted is None:
                raise TaskNotFoundError(task_id)
            position = repository.next_position(
                connection, context.workspace_id, str(deleted["status"])
            )
            connection.execute(
                "UPDATE tasks SET position=? WHERE workspace_id=? AND id=?",
                (position, context.workspace_id, task_id),
            )
            restored = repository.restore_task(
                connection,
                context.workspace_id,
                task_id,
                actor_id=context.user_id,
                timestamp=self._timestamp(),
            )
            assert restored is not None
            result = self._with_topics(connection, context.workspace_id, restored)
        self._activity(context, "calendar.restored", result)
        return result
