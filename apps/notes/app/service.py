from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any

from apps.notes.app import repository
from apps.notes.app.database import database_connection
from apps.notes.app.schemas import NoteCreate, NoteUpdate, SourceReferenceInput
from shared.source_references import (
    ResolvedSource,
    SourceReference,
    SourceReferenceResolver,
)


class NoteNotFoundError(LookupError):
    pass


class LinkNotFoundError(LookupError):
    pass


class DuplicateLinkError(ValueError):
    pass


class SourceNotFoundError(LookupError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class NotesService:
    def __init__(
        self,
        database_path: Path,
        *,
        clock: Callable[[], datetime] = utc_now,
        source_resolver: SourceReferenceResolver | None = None,
    ) -> None:
        self.database_path = database_path
        self.clock = clock
        self.source_resolver = source_resolver or SourceReferenceResolver()

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
        resolved_links = [self._resolve_source(link) for link in payload.links]
        references = [
            (item.reference.source_app, item.reference.source_type, item.reference.source_id)
            for item in resolved_links
        ]
        if len(references) != len(set(references)):
            raise DuplicateLinkError("Note contains duplicate source links")
        with database_connection(self.database_path) as connection:
            note = repository.create_note(
                connection,
                title=payload.title,
                content=payload.content,
                note_type=payload.note_type,
                is_pinned=payload.is_pinned,
                created_at=timestamp,
                updated_at=timestamp,
            )
            for source in resolved_links:
                repository.create_link(
                    connection,
                    note_id=note["id"],
                    source_app=source.reference.source_app,
                    source_type=source.reference.source_type,
                    source_id=source.reference.source_id,
                    source_title_snapshot=source.title,
                    created_at=timestamp,
                )
            return note

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

    def _reference(self, payload: SourceReferenceInput) -> SourceReference:
        return SourceReference(
            source_app=payload.source_app,
            source_type=payload.source_type,
            source_id=payload.source_id,
        )

    def _resolve_source(self, payload: SourceReferenceInput) -> ResolvedSource:
        reference = self._reference(payload)
        resolved = self.source_resolver.resolve(reference)
        if resolved is None:
            raise SourceNotFoundError(reference)
        return resolved

    def create_link(
        self,
        note_id: int,
        payload: SourceReferenceInput,
    ) -> dict[str, Any]:
        resolved = self._resolve_source(payload)
        with database_connection(self.database_path) as connection:
            if repository.get_note(connection, note_id) is None:
                raise NoteNotFoundError(note_id)
            try:
                link = repository.create_link(
                    connection,
                    note_id=note_id,
                    source_app=resolved.reference.source_app,
                    source_type=resolved.reference.source_type,
                    source_id=resolved.reference.source_id,
                    source_title_snapshot=resolved.title,
                    created_at=self._timestamp(),
                )
            except sqlite3.IntegrityError as error:
                raise DuplicateLinkError("Source is already linked to this Note") from error
        return {**link, "orphan": False}

    def list_links(self, note_id: int) -> list[dict[str, Any]]:
        with database_connection(self.database_path) as connection:
            if repository.get_note(connection, note_id) is None:
                raise NoteNotFoundError(note_id)
            links = repository.list_links(connection, note_id)
        result = []
        for link in links:
            reference = SourceReference(
                source_app=link["source_app"],
                source_type=link["source_type"],
                source_id=link["source_id"],
            )
            try:
                orphan = self.source_resolver.resolve(reference) is None
            except ValueError:
                orphan = True
            result.append({**link, "orphan": orphan})
        return result

    def delete_link(self, note_id: int, link_id: int) -> None:
        with database_connection(self.database_path) as connection:
            if repository.get_note(connection, note_id) is None:
                raise NoteNotFoundError(note_id)
            if not repository.delete_link(connection, note_id, link_id):
                raise LinkNotFoundError(link_id)
