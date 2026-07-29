from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.notes.app import repository
from apps.notes.app.database import database_connection
from apps.notes.app.schemas import NoteCreate, NoteUpdate


class NoteNotFoundError(LookupError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class NotesService:
    def __init__(
        self,
        database_path: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database_path = database_path
        self.clock = clock

    def _timestamp(self) -> str:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Notes clock must return a timezone-aware datetime")
        return value.astimezone(UTC).isoformat(timespec="seconds")

    def list_notes(
        self,
        *,
        query: str = "",
        note_type: str | None = None,
        pinned: bool | None = None,
        source_app: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with database_connection(self.database_path) as connection:
            return repository.list_notes(
                connection,
                query=query,
                note_type=note_type,
                pinned=pinned,
                source_app=source_app,
                source_type=source_type,
                source_id=source_id,
                limit=limit,
                offset=offset,
            )

    def get_note(self, note_id: int) -> dict[str, Any]:
        with database_connection(self.database_path) as connection:
            note = repository.get_note(connection, note_id)
        if note is None:
            raise NoteNotFoundError(note_id)
        return note

    def create_note(self, payload: NoteCreate) -> dict[str, Any]:
        timestamp = self._timestamp()
        with database_connection(self.database_path) as connection:
            return repository.create_note(
                connection,
                title=payload.title,
                content=payload.content,
                note_type=payload.note_type,
                is_pinned=payload.is_pinned,
                created_at=timestamp,
                updated_at=timestamp,
            )

    def update_note(
        self,
        note_id: int,
        payload: NoteUpdate,
    ) -> dict[str, Any]:
        with database_connection(self.database_path) as connection:
            note = repository.update_note(
                connection,
                note_id,
                title=payload.title,
                content=payload.content,
                note_type=payload.note_type,
                is_pinned=payload.is_pinned,
                updated_at=self._timestamp(),
            )
        if note is None:
            raise NoteNotFoundError(note_id)
        return note

    def delete_note(self, note_id: int) -> None:
        with database_connection(self.database_path) as connection:
            deleted = repository.delete_note(connection, note_id)
        if not deleted:
            raise NoteNotFoundError(note_id)
