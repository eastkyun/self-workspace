from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from access.context import PermissionSnapshot, WorkspaceContext
from access.errors import PermissionDenied
from apps.calendar.app.main import initialize_database
from apps.calendar.app.service import CalendarService, TaskNotFoundError
from apps.calendar.migrations import MIGRATIONS, TARGET_VERSION
from shared.migrations import migrate


def context(workspace_id: str, user_id: str) -> WorkspaceContext:
    return WorkspaceContext(
        user_id=user_id,
        workspace_id=workspace_id,
        membership_id=f"membership-{workspace_id}-{user_id}",
        role_key="member",
        enabled_app_ids=("calendar-app",),
        authorization_version=1,
    )


class Policy:
    def __init__(self, permissions: set[str]) -> None:
        self.permissions = frozenset(permissions)

    def snapshot(self, ctx: WorkspaceContext) -> PermissionSnapshot:
        return PermissionSnapshot(
            ctx.workspace_id,
            ctx.membership_id,
            ctx.role_key,
            1,
            (("calendar", "calendar-app"),),
            self.permissions,
            frozenset(),
            frozenset(),
            frozenset(),
            frozenset({"calendar"}),
        )

    def require(self, ctx, permission_code=None, resource_owner_id=None, *, app_key=None, snapshot=None):
        if permission_code and not self.authorize(
            ctx, permission_code, resource_owner_id, snapshot=snapshot
        ):
            raise PermissionDenied()
        return self.snapshot(ctx)

    def authorize(self, ctx, permission_code, resource_owner_id=None, *, snapshot=None):
        if permission_code not in self.permissions:
            return False
        return not permission_code.endswith("_own") or resource_owner_id == ctx.user_id


@pytest.fixture
def calendar_service(tmp_path):
    path = tmp_path / "calendar.db"
    initialize_database(path, create_backup=False)
    workspace_id, actor_id = str(uuid4()), str(uuid4())
    import sqlite3

    with sqlite3.connect(path) as connection:
        migrate(
            connection,
            target_version=TARGET_VERSION,
            migrations=MIGRATIONS,
            context={"workspace_id": workspace_id, "actor_id": actor_id},
        )
    policy = Policy(
        {
            "calendar.read_all",
            "calendar.create",
            "calendar.update_own",
            "calendar.delete_own",
            "calendar.manage",
        }
    )
    service = CalendarService(
        path,
        authorization=policy,
        clock=lambda: datetime(2026, 8, 6, tzinfo=UTC),
    )
    return service, workspace_id


def create(service: CalendarService, ctx: WorkspaceContext):
    return service.create_task(
        ctx,
        title="Private event",
        description="",
        status="todo",
        all_day=False,
        start_at="2026-08-06T09:00:00+09:00",
        due_at="2026-08-06T10:00:00+09:00",
    )


def test_task_service_hides_cross_workspace_ids(calendar_service):
    service, workspace_a = calendar_service
    owner = context(workspace_a, "owner")
    workspace_b = context(str(uuid4()), "other")
    task = create(service, owner)

    with pytest.raises(TaskNotFoundError):
        service.get_task(workspace_b, task["id"])
    with pytest.raises(TaskNotFoundError):
        service.delete_task(workspace_b, task["id"])
    assert service.list_tasks(workspace_b) == []


def test_own_permissions_actor_tracking_and_restore(calendar_service):
    service, workspace_id = calendar_service
    owner = context(workspace_id, "owner")
    other = context(workspace_id, "other")
    task = create(service, owner)
    update = dict(
        title="Updated",
        description="body",
        status="doing",
        position=0,
        all_day=False,
        start_at=task["start_at"],
        due_at=task["due_at"],
    )

    with pytest.raises(PermissionDenied):
        service.update_task(other, task["id"], **update)
    updated = service.update_task(owner, task["id"], **update)
    assert updated["created_by"] == updated["updated_by"] == owner.user_id
    service.delete_task(owner, task["id"])
    with pytest.raises(TaskNotFoundError):
        service.get_task(owner, task["id"])
    restored = service.restore_task(other, task["id"])
    assert restored["updated_by"] == other.user_id


def test_create_and_restore_enforce_capabilities(tmp_path):
    path = tmp_path / "calendar.db"
    initialize_database(path, create_backup=False)
    workspace_id, actor_id = str(uuid4()), str(uuid4())
    import sqlite3

    with sqlite3.connect(path) as connection:
        migrate(
            connection,
            target_version=TARGET_VERSION,
            migrations=MIGRATIONS,
            context={"workspace_id": workspace_id, "actor_id": actor_id},
        )
    ctx = context(workspace_id, actor_id)
    service = CalendarService(path, authorization=Policy({"calendar.read_all"}))
    with pytest.raises(PermissionDenied):
        create(service, ctx)
