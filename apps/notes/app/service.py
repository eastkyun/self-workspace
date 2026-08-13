from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from typing import Any

from access.context import WorkspaceContext
from access.errors import PermissionDenied
from access.service import AuthorizationService
from apps.notes.app import repository
from apps.notes.app.database import database_connection
from apps.notes.app.schemas import NoteCreate, NoteUpdate, SourceReferenceInput
from shared.source_references import ResolvedSource, SourceReference, SourceReferenceResolver
from shared.activity import ActivitySink, NullActivitySink


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
        authorization: AuthorizationService,
        clock: Callable[[], datetime] = utc_now,
        source_resolver: SourceReferenceResolver | None = None,
        source_resolver_factory: Callable[[WorkspaceContext], SourceReferenceResolver]
        | None = None,
        activity_sink: ActivitySink | None = None,
    ) -> None:
        self.database_path = database_path
        self.authorization = authorization
        self.clock = clock
        self.source_resolver = source_resolver or SourceReferenceResolver()
        self.source_resolver_factory = source_resolver_factory
        self.activity_sink = activity_sink or NullActivitySink()

    def _activity(self, context: WorkspaceContext, action: str, note: dict[str, Any]) -> None:
        self.activity_sink.record(
            context, app_key="notes", action=action, resource_type="note",
            resource_id=str(note["id"]),
        )

    def _resolver(self, context: WorkspaceContext) -> SourceReferenceResolver:
        if self.source_resolver_factory is not None:
            return self.source_resolver_factory(context)
        return self.source_resolver

    def _timestamp(self) -> str:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Notes clock must return a timezone-aware datetime")
        return value.astimezone(UTC).isoformat(timespec="seconds")

    def _require_read(self, context: WorkspaceContext):
        return self.authorization.require(
            context, "notes.read_all", app_key="notes"
        )

    def require_create(self, context: WorkspaceContext) -> None:
        """Fail before collection work when the actor cannot create Notes."""
        snapshot = self._require_read(context)
        if not self.authorization.authorize(
            context, "notes.create", snapshot=snapshot
        ):
            raise PermissionDenied("permission denied")

    def _require_resource_action(
        self,
        context: WorkspaceContext,
        action: str,
        resource_owner_id: str,
    ) -> None:
        snapshot = self._require_read(context)
        if self.authorization.authorize(
            context, f"notes.{action}_all", snapshot=snapshot
        ):
            return
        if self.authorization.authorize(
            context,
            f"notes.{action}_own",
            resource_owner_id,
            snapshot=snapshot,
        ):
            return
        raise PermissionDenied("permission denied")

    def list_notes(
        self,
        context: WorkspaceContext,
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
        self._require_read(context)
        source_values = (source_app, source_type, source_id)
        if any(value is not None for value in source_values):
            if not all(value is not None for value in source_values):
                raise ValueError("source_app, source_type, and source_id are required together")
            self._resolver(context).resolve(
                self._reference(SourceReferenceInput(
                    source_app=source_app, source_type=source_type, source_id=source_id
                ))
            )
        with database_connection(self.database_path) as connection:
            return repository.list_notes(
                connection,
                context.workspace_id,
                query=query,
                note_type=note_type,
                pinned=pinned,
                source_app=source_app,
                source_type=source_type,
                source_id=source_id,
                limit=limit,
                offset=offset,
            )

    def get_note(self, context: WorkspaceContext, note_id: int) -> dict[str, Any]:
        self._require_read(context)
        with database_connection(self.database_path) as connection:
            note = repository.get_note(connection, context.workspace_id, note_id)
        if note is None:
            raise NoteNotFoundError(note_id)
        return note

    def create_note(
        self,
        context: WorkspaceContext,
        payload: NoteCreate,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        self.require_create(context)
        if idempotency_key is not None:
            if not idempotency_key or idempotency_key.strip() != idempotency_key:
                raise ValueError("idempotency key must not be empty or padded")
            if len(idempotency_key) > 255:
                raise ValueError("idempotency key must not exceed 255 characters")
        timestamp = self._timestamp()
        resolved_links = [self._resolve_source(context, link) for link in payload.links]
        references = [
            (item.reference.source_app, item.reference.source_type, item.reference.source_id)
            for item in resolved_links
        ]
        if len(references) != len(set(references)):
            raise DuplicateLinkError("Note contains duplicate source links")
        created = True
        with database_connection(self.database_path) as connection:
            if idempotency_key is not None:
                # Serialize the read/create pair. SQLite then makes a retry in
                # another process observe the committed key instead of
                # creating a second Note.
                connection.execute("BEGIN IMMEDIATE")
                existing = repository.get_note_for_idempotency_key(
                    connection, context.workspace_id, idempotency_key
                )
                if existing is not None:
                    note = existing
                    created = False
                else:
                    note = repository.create_note(
                        connection,
                        context.workspace_id,
                        actor_id=context.user_id,
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
                            context.workspace_id,
                            note_id=note["id"],
                            source_app=source.reference.source_app,
                            source_type=source.reference.source_type,
                            source_id=source.reference.source_id,
                            source_title_snapshot=source.title,
                            created_at=timestamp,
                        )
                    repository.create_idempotency_key(
                        connection,
                        context.workspace_id,
                        key=idempotency_key,
                        note_id=note["id"],
                        created_at=timestamp,
                    )
            else:
                note = repository.create_note(
                    connection,
                    context.workspace_id,
                    actor_id=context.user_id,
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
                        context.workspace_id,
                        note_id=note["id"],
                        source_app=source.reference.source_app,
                        source_type=source.reference.source_type,
                        source_id=source.reference.source_id,
                        source_title_snapshot=source.title,
                        created_at=timestamp,
                    )
        if created:
            self._activity(context, "notes.created", note)
        return note

    def update_note(
        self, context: WorkspaceContext, note_id: int, payload: NoteUpdate
    ) -> dict[str, Any]:
        with database_connection(self.database_path) as connection:
            note = repository.get_note(connection, context.workspace_id, note_id)
            if note is None:
                raise NoteNotFoundError(note_id)
            self._require_resource_action(context, "update", note["created_by"])
            updated = repository.update_note(
                connection,
                context.workspace_id,
                note_id,
                actor_id=context.user_id,
                title=payload.title,
                content=payload.content,
                note_type=payload.note_type,
                is_pinned=payload.is_pinned,
                updated_at=self._timestamp(),
            )
            assert updated is not None
        self._activity(context, "notes.updated", updated)
        return updated

    def delete_note(self, context: WorkspaceContext, note_id: int) -> None:
        with database_connection(self.database_path) as connection:
            note = repository.get_note(connection, context.workspace_id, note_id)
            if note is None:
                raise NoteNotFoundError(note_id)
            self._require_resource_action(context, "delete", note["created_by"])
            if not repository.soft_delete_note(
                connection,
                context.workspace_id,
                note_id,
                actor_id=context.user_id,
                deleted_at=self._timestamp(),
            ):
                raise NoteNotFoundError(note_id)
        self._activity(context, "notes.deleted", note)

    def restore_note(self, context: WorkspaceContext, note_id: int) -> dict[str, Any]:
        snapshot = self._require_read(context)
        if not self.authorization.authorize(context, "notes.manage", snapshot=snapshot):
            raise PermissionDenied("permission denied")
        with database_connection(self.database_path) as connection:
            if repository.get_deleted_note(connection, context.workspace_id, note_id) is None:
                raise NoteNotFoundError(note_id)
            restored = repository.restore_note(
                connection,
                context.workspace_id,
                note_id,
                actor_id=context.user_id,
                updated_at=self._timestamp(),
            )
            assert restored is not None
        self._activity(context, "notes.restored", restored)
        return restored

    @staticmethod
    def _reference(payload: SourceReferenceInput) -> SourceReference:
        return SourceReference(
            source_app=payload.source_app,
            source_type=payload.source_type,
            source_id=payload.source_id,
        )

    def _resolve_source(
        self, context: WorkspaceContext, payload: SourceReferenceInput
    ) -> ResolvedSource:
        reference = self._reference(payload)
        resolved = self._resolver(context).resolve(reference)
        if resolved is None:
            raise SourceNotFoundError(reference)
        return resolved

    def create_link(
        self, context: WorkspaceContext, note_id: int, payload: SourceReferenceInput
    ) -> dict[str, Any]:
        resolved = self._resolve_source(context, payload)
        with database_connection(self.database_path) as connection:
            note = repository.get_note(connection, context.workspace_id, note_id)
            if note is None:
                raise NoteNotFoundError(note_id)
            self._require_resource_action(context, "update", note["created_by"])
            try:
                link = repository.create_link(
                    connection,
                    context.workspace_id,
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

    def list_links(self, context: WorkspaceContext, note_id: int) -> list[dict[str, Any]]:
        self._require_read(context)
        with database_connection(self.database_path) as connection:
            if repository.get_note(connection, context.workspace_id, note_id) is None:
                raise NoteNotFoundError(note_id)
            links = repository.list_links(connection, context.workspace_id, note_id)
        result = []
        for link in links:
            reference = SourceReference(
                source_app=link["source_app"],
                source_type=link["source_type"],
                source_id=link["source_id"],
            )
            try:
                orphan = self._resolver(context).resolve(reference) is None
            except ValueError:
                orphan = True
            result.append({**link, "orphan": orphan})
        return result

    def delete_link(self, context: WorkspaceContext, note_id: int, link_id: int) -> None:
        with database_connection(self.database_path) as connection:
            note = repository.get_note(connection, context.workspace_id, note_id)
            if note is None:
                raise NoteNotFoundError(note_id)
            self._require_resource_action(context, "update", note["created_by"])
            if not repository.delete_link(
                connection, context.workspace_id, note_id, link_id
            ):
                raise LinkNotFoundError(link_id)
