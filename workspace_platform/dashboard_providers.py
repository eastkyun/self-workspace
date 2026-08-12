"""Built-in dashboard widget providers composed by the portal."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from access.context import WorkspaceContext
from apps.calendar.app.service import CalendarService
from apps.notes.app.service import NotesService
from identity.database import database_connection

from .search import WorkspaceSearchService
from .user_state import WorkspaceUserStateService


EMPTY_SETTINGS = {"type": "object", "properties": {}, "additionalProperties": False}


@dataclass(frozen=True, slots=True)
class RecentNotesWidget:
    service: NotesService
    widget_key: str = "notes.recent"
    app_key: str = "notes"
    display_name: str = "최근 수정 Notes"
    required_permissions: tuple[str, ...] = ("notes.read_all",)
    default_size: str = "MEDIUM"
    settings_schema: dict[str, Any] = field(default_factory=lambda: dict(EMPTY_SETTINGS))

    def load(self, context: WorkspaceContext, settings: Mapping[str, Any], limit: int) -> object:
        return [_note_projection(context, note) for note in self.service.list_notes(context, limit=limit)]


@dataclass(frozen=True, slots=True)
class MyNotesWidget:
    service: NotesService
    widget_key: str = "notes.mine"
    app_key: str = "notes"
    display_name: str = "내 Notes"
    required_permissions: tuple[str, ...] = ("notes.read_all",)
    default_size: str = "MEDIUM"
    settings_schema: dict[str, Any] = field(default_factory=lambda: dict(EMPTY_SETTINGS))

    def load(self, context: WorkspaceContext, settings: Mapping[str, Any], limit: int) -> object:
        notes = self.service.list_notes(context, limit=100)
        return [
            _note_projection(context, note)
            for note in notes
            if note.get("created_by") == context.user_id
        ][:limit]


def _note_projection(context: WorkspaceContext, note: Mapping[str, Any]) -> dict[str, object]:
    return {
        "id": str(note["id"]),
        "title": note["title"],
        "href": f"/w/{context.workspace_id}/notes?note_id={note['id']}",
        "updated_at": note.get("updated_at"),
    }


@dataclass(frozen=True, slots=True)
class TodayCalendarWidget:
    service: CalendarService
    identity_database_path: Path
    widget_key: str = "calendar.today"
    app_key: str = "calendar"
    display_name: str = "오늘 일정"
    required_permissions: tuple[str, ...] = ("calendar.read_all",)
    default_size: str = "LARGE"
    settings_schema: dict[str, Any] = field(default_factory=lambda: dict(EMPTY_SETTINGS))

    def load(self, context: WorkspaceContext, settings: Mapping[str, Any], limit: int) -> object:
        with database_connection(self.identity_database_path) as tx:
            row = tx.execute("SELECT timezone FROM workspaces WHERE id=?", (context.workspace_id,)).fetchone()
        try:
            zone = ZoneInfo(row["timezone"] if row else "Asia/Seoul")
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("Asia/Seoul")
        today = datetime.now(zone).date().isoformat()
        tasks = [
            task for task in self.service.list_tasks(context)
            if str(task.get("start_at") or "")[:10] == today
            or str(task.get("due_at") or "")[:10] == today
        ][:limit]
        return [
            {
                "id": str(task["id"]),
                "title": task["title"],
                "status": task["status"],
                "all_day": bool(task["all_day"]),
                "start_at": task.get("start_at"),
                "due_at": task.get("due_at"),
                "href": f"/w/{context.workspace_id}/calendar",
            }
            for task in tasks
        ]


@dataclass(frozen=True, slots=True)
class FavoritesWidget:
    service: WorkspaceUserStateService
    widget_key: str = "favorites"
    app_key: str = "home"
    display_name: str = "즐겨찾기"
    required_permissions: tuple[str, ...] = ()
    default_size: str = "MEDIUM"
    settings_schema: dict[str, Any] = field(default_factory=lambda: dict(EMPTY_SETTINGS))

    def load(self, context: WorkspaceContext, settings: Mapping[str, Any], limit: int) -> object:
        return self.service.list_favorites(context, limit=limit)


@dataclass(frozen=True, slots=True)
class RecentViewsWidget:
    service: WorkspaceUserStateService
    widget_key: str = "recent_views"
    app_key: str = "home"
    display_name: str = "최근 본 항목"
    required_permissions: tuple[str, ...] = ()
    default_size: str = "MEDIUM"
    settings_schema: dict[str, Any] = field(default_factory=lambda: dict(EMPTY_SETTINGS))

    def load(self, context: WorkspaceContext, settings: Mapping[str, Any], limit: int) -> object:
        return self.service.list_recent(context, limit=limit)


@dataclass(frozen=True, slots=True)
class ActivitySummaryWidget:
    service: WorkspaceUserStateService
    widget_key: str = "workspace.activity"
    app_key: str = "home"
    display_name: str = "최근 Workspace 활동"
    required_permissions: tuple[str, ...] = ()
    default_size: str = "SMALL"
    settings_schema: dict[str, Any] = field(default_factory=lambda: dict(EMPTY_SETTINGS))

    def load(self, context: WorkspaceContext, settings: Mapping[str, Any], limit: int) -> object:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        activity = self.service.list_activity(context, limit=100)
        recent = []
        for item in activity:
            try:
                timestamp = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                continue
            if timestamp >= cutoff:
                recent.append(item)
        return {"count": len(recent), "items": recent[:limit]}


@dataclass(frozen=True, slots=True)
class SearchShortcutWidget:
    service: WorkspaceSearchService
    widget_key: str = "workspace.search"
    app_key: str = "search"
    display_name: str = "Workspace 검색"
    required_permissions: tuple[str, ...] = ("search.read_all",)
    default_size: str = "SMALL"
    settings_schema: dict[str, Any] = field(default_factory=lambda: dict(EMPTY_SETTINGS))

    def load(self, context: WorkspaceContext, settings: Mapping[str, Any], limit: int) -> object:
        return {"href": f"/w/{context.workspace_id}/search"}


__all__ = [
    "ActivitySummaryWidget",
    "FavoritesWidget",
    "MyNotesWidget",
    "RecentNotesWidget",
    "RecentViewsWidget",
    "SearchShortcutWidget",
    "TodayCalendarWidget",
]
