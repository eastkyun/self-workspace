from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from access.context import WorkspaceContext


@dataclass(frozen=True, slots=True)
class NoteSnapshot:
    """Saved Note state copied into an immutable publication version."""

    note_id: int
    title: str
    markdown: str
    updated_at: str


@runtime_checkable
class NoteSnapshotReader(Protocol):
    def get_snapshot(self, note_id: int) -> NoteSnapshot | None: ...


@runtime_checkable
class NotesApplicationService(Protocol):
    """Narrow same-process boundary implemented by NotesService."""

    def get_note(self, context: WorkspaceContext, note_id: int) -> dict[str, Any]: ...


class NotesServiceSnapshotReader:
    """Adapt the official Notes application service without internal HTTP."""

    def __init__(
        self,
        notes_service: NotesApplicationService,
        context: WorkspaceContext,
    ) -> None:
        self._notes_service = notes_service
        self._context = context

    def get_snapshot(self, note_id: int) -> NoteSnapshot | None:
        try:
            note = self._notes_service.get_note(self._context, note_id)
        except LookupError:
            return None
        return NoteSnapshot(
            note_id=note["id"],
            title=note["title"],
            markdown=note["content"],
            updated_at=note["updated_at"],
        )


__all__ = (
    "NoteSnapshot",
    "NoteSnapshotReader",
    "NotesApplicationService",
    "NotesServiceSnapshotReader",
)
