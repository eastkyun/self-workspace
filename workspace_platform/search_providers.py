"""Concrete workspace-search adapters for built-in applications."""

from __future__ import annotations

from dataclasses import dataclass

from access.context import WorkspaceContext
from apps.calendar.app.service import CalendarService
from apps.notes.app.service import NotesService
from publishing.database import database_connection as publishing_connection

from .search import SearchResult


def _score(query: str, title: str, body: str = "") -> float:
    needle = query.casefold()
    heading = title.casefold()
    if heading == needle:
        return 100.0
    if heading.startswith(needle):
        return 75.0
    if needle in heading:
        return 50.0
    return 20.0 if needle in body.casefold() else 0.0


@dataclass(frozen=True, slots=True)
class NotesWorkspaceSearchProvider:
    service: NotesService
    key: str = "notes"
    app_key: str = "notes"
    required_permissions: tuple[str, ...] = ("notes.read_all",)

    def search(
        self,
        context: WorkspaceContext,
        query: str,
        limit: int,
        offset: int,
    ) -> tuple[SearchResult, ...]:
        notes = self.service.list_notes(
            context,
            query=query,
            limit=limit,
            offset=offset,
        )
        return tuple(
            SearchResult(
                workspace_id=context.workspace_id,
                resource_id=str(note["id"]),
                title=note["title"],
                summary=note.get("content", "")[:240],
                url=f"/w/{context.workspace_id}/notes?note_id={note['id']}",
                score=_score(query, note["title"], note.get("content", "")),
                updated_at=note.get("updated_at"),
            )
            for note in notes
        )


@dataclass(frozen=True, slots=True)
class CalendarWorkspaceSearchProvider:
    service: CalendarService
    key: str = "calendar"
    app_key: str = "calendar"
    required_permissions: tuple[str, ...] = ("calendar.read_all",)

    def search(
        self,
        context: WorkspaceContext,
        query: str,
        limit: int,
        offset: int,
    ) -> tuple[SearchResult, ...]:
        needle = query.casefold()
        matches = [
            task
            for task in self.service.list_tasks(context)
            if needle in task["title"].casefold()
            or needle in task.get("description", "").casefold()
        ]
        matches.sort(
            key=lambda task: (
                -_score(query, task["title"], task.get("description", "")),
                task["title"].casefold(),
                int(task["id"]),
            )
        )
        return tuple(
            SearchResult(
                workspace_id=context.workspace_id,
                resource_id=str(task["id"]),
                title=task["title"],
                summary=task.get("description", "")[:240],
                url=f"/w/{context.workspace_id}/calendar",
                score=_score(query, task["title"], task.get("description", "")),
                updated_at=task.get("updated_at"),
            )
            for task in matches[offset : offset + limit]
        )


@dataclass(frozen=True, slots=True)
class PublishingWorkspaceSearchProvider:
    database_path: str
    key: str = "publishing"
    app_key: str = "publishing"
    required_permissions: tuple[str, ...] = ("publishing.read_all",)

    def search(
        self,
        context: WorkspaceContext,
        query: str,
        limit: int,
        offset: int,
    ) -> tuple[SearchResult, ...]:
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with publishing_connection(self.database_path) as connection:
            rows = connection.execute(
                """SELECT id, public_id, slug, title, summary, updated_at
                FROM publications
                WHERE workspace_id=? AND status='published'
                  AND (title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')
                ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?""",
                (context.workspace_id, pattern, pattern, limit, offset),
            ).fetchall()
        return tuple(
            SearchResult(
                workspace_id=context.workspace_id,
                resource_id=str(row["id"]),
                title=row["title"],
                summary=row["summary"],
                url=f"/p/{row['public_id']}/{row['slug']}",
                score=_score(query, row["title"], row["summary"]),
                updated_at=row["updated_at"],
            )
            for row in rows
        )


__all__ = [
    "CalendarWorkspaceSearchProvider",
    "NotesWorkspaceSearchProvider",
    "PublishingWorkspaceSearchProvider",
]
