from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest

from apps.calendar.app.internal_link_provider import CalendarInternalLinkProvider
from apps.calendar.app.main import initialize_database
from apps.calendar.app.source_provider import CalendarEventSourceProvider
from apps.calendar.migrations import MIGRATIONS, TARGET_VERSION
from shared.migrations import migrate
from shared.source_references import SourceReference


def _tenant_calendar(tmp_path):
    path = tmp_path / "calendar.db"
    initialize_database(path, create_backup=False)
    workspace_a, workspace_b, actor = map(str, (uuid4(), uuid4(), uuid4()))
    with sqlite3.connect(path) as connection:
        migrate(
            connection,
            target_version=TARGET_VERSION,
            migrations=MIGRATIONS,
            context={"workspace_id": workspace_a, "actor_id": actor},
        )
        connection.execute(
            """INSERT INTO tasks (
                title, description, status, position, all_day, start_at, due_at,
                created_at, updated_at, workspace_id, created_by, updated_by, deleted_at
            ) VALUES ('Workspace B event', '', 'todo', 0, 0, ?, ?, 'c', 'u', ?, ?, ?, NULL)""",
            (
                "2026-08-06T09:00:00+09:00",
                "2026-08-06T10:00:00+09:00",
                workspace_b,
                actor,
                actor,
            ),
        )
        event_b = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """INSERT INTO tasks (
                title, description, status, position, all_day, start_at, due_at,
                created_at, updated_at, workspace_id, created_by, updated_by, deleted_at
            ) VALUES ('Deleted A event', '', 'todo', 0, 0, ?, ?, 'c', 'u', ?, ?, ?, 'deleted')""",
            (
                "2026-08-07T09:00:00+09:00",
                "2026-08-07T10:00:00+09:00",
                workspace_a,
                actor,
                actor,
            ),
        )
        deleted_a = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    return path, workspace_a, workspace_b, event_b, deleted_a


def test_source_provider_hides_cross_workspace_and_deleted_events(tmp_path):
    path, workspace_a, workspace_b, event_b, deleted_a = _tenant_calendar(tmp_path)
    reference_b = SourceReference("calendar", "event", str(event_b))

    assert CalendarEventSourceProvider(path, workspace_a).resolve(reference_b) is None
    assert CalendarEventSourceProvider(path, workspace_b).resolve(reference_b).title == "Workspace B event"
    assert CalendarEventSourceProvider(path, workspace_a).resolve(
        SourceReference("calendar", "event", str(deleted_a))
    ) is None


def test_internal_link_provider_uses_canonical_workspace_route(tmp_path):
    path, workspace_a, workspace_b, event_b, deleted_a = _tenant_calendar(tmp_path)

    assert CalendarInternalLinkProvider(path, workspace_a).resolve_many(
        (str(event_b), str(deleted_a))
    ) == {}
    link = CalendarInternalLinkProvider(path, workspace_b).resolve_many((str(event_b),))[
        str(event_b)
    ]
    assert link.href == f"/w/{workspace_b}/calendar?event_id={event_b}"


def test_tenant_database_rejects_unscoped_calendar_providers(tmp_path):
    path, _, _, event_b, _ = _tenant_calendar(tmp_path)

    with pytest.raises(RuntimeError, match="workspace_id is required"):
        CalendarEventSourceProvider(path).resolve(
            SourceReference("calendar", "event", str(event_b))
        )
    with pytest.raises(RuntimeError, match="workspace_id is required"):
        CalendarInternalLinkProvider(path).resolve_many((str(event_b),))
