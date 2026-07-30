from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from publishing import repository
from publishing.contracts import PublicationSnapshot, PublicationType
from publishing.database import database_connection
from publishing.events import (
    AfterCommitEventDispatcher,
    PublicationArchived,
    PublicationCreated,
    PublicationDraftDiscarded,
    PublicationEvent,
    PublicationPublished,
    PublicationRestored,
    PublicationVersionCreated,
)
from publishing.registry import PublicationTypeRegistry
from publishing.schemas import PublicPublicationContent
from publishing.source_notes import NoteSnapshot, NoteSnapshotReader


class PublishingError(Exception):
    pass


class PublicationNotFoundError(PublishingError, LookupError):
    pass


class PublicationVersionNotFoundError(PublishingError, LookupError):
    pass


class SourceNoteNotFoundError(PublishingError, LookupError):
    pass


class PublicationTypeNotFoundError(PublishingError, LookupError):
    pass


class PublicationTypeDisabledError(PublishingError):
    pass


class InvalidSlugError(PublishingError, ValueError):
    pass


class DuplicateSlugError(PublishingError, ValueError):
    pass


class InvalidPublicationTransitionError(PublishingError, ValueError):
    pass


class PublicationValidationError(PublishingError, ValueError):
    def __init__(self, issues: tuple[str, ...]) -> None:
        self.issues = issues
        super().__init__("publication validation failed: " + "; ".join(issues))


class PublishingPersistenceError(PublishingError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


class PublishingService:
    """Own immutable publication lifecycle and pointer invariants."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        registry: PublicationTypeRegistry,
        note_reader: NoteSnapshotReader,
        clock: Callable[[], datetime] = utc_now,
        event_dispatcher: AfterCommitEventDispatcher | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.registry = registry
        self.note_reader = note_reader
        self.clock = clock
        self.event_dispatcher = event_dispatcher or AfterCommitEventDispatcher()

    def _timestamp(self) -> str:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Publishing clock must return a timezone-aware datetime")
        return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _require_provider(
        self,
        type_id: str,
        *,
        enabled: bool = False,
    ) -> PublicationType:
        provider = self.registry.get(type_id)
        if provider is None:
            raise PublicationTypeNotFoundError(type_id)
        if enabled and not provider.enabled:
            raise PublicationTypeDisabledError(type_id)
        return provider

    def _validate_slug(self, provider: PublicationType, slug: str) -> str:
        policy = provider.slug_policy
        if not isinstance(slug, str):
            raise InvalidSlugError("slug must be a string")
        if not policy.min_length <= len(slug) <= policy.max_length:
            raise InvalidSlugError(
                f"slug length must be {policy.min_length}-{policy.max_length}"
            )
        if slug in policy.reserved_words:
            raise InvalidSlugError("slug is reserved")
        if re.fullmatch(policy.pattern, slug) is None:
            raise InvalidSlugError("slug is not canonical lowercase ASCII")
        return slug

    def _require_source_note(self, note_id: int) -> NoteSnapshot:
        snapshot = self.note_reader.get_snapshot(note_id)
        if snapshot is None:
            raise SourceNoteNotFoundError(note_id)
        return snapshot

    @staticmethod
    def _require_publication(
        connection: sqlite3.Connection,
        publication_id: int,
    ) -> dict[str, Any]:
        publication = repository.get_publication(connection, publication_id)
        if publication is None:
            raise PublicationNotFoundError(publication_id)
        return publication

    @staticmethod
    def _require_version(
        connection: sqlite3.Connection,
        publication_id: int,
        version: int,
    ) -> dict[str, Any]:
        snapshot = repository.get_publication_version(
            connection, publication_id, version
        )
        if snapshot is None:
            raise PublicationVersionNotFoundError((publication_id, version))
        return snapshot

    @staticmethod
    def _projection(
        connection: sqlite3.Connection,
        publication: dict[str, Any],
        *,
        draft_version: int | None,
        published_version: int | None,
        latest_version: int,
    ) -> dict[str, Any]:
        projection_version = draft_version or published_version or latest_version
        return PublishingService._require_version(
            connection, publication["id"], projection_version
        )

    def _dispatch(self, events: tuple[PublicationEvent, ...]) -> None:
        self.event_dispatcher.dispatch(events)

    def create_draft_from_note(
        self,
        *,
        note_id: int,
        type_id: str,
        slug: str,
        summary: str = "",
    ) -> dict[str, Any]:
        provider = self._require_provider(type_id, enabled=True)
        canonical_slug = self._validate_slug(provider, slug)
        source = self._require_source_note(note_id)
        timestamp = self._timestamp()
        try:
            with database_connection(self.database_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                publication = repository.create_publication(
                    connection,
                    note_id=source.note_id,
                    type_id=provider.type_id,
                    slug=canonical_slug,
                    title=source.title,
                    summary=summary,
                    markdown=source.markdown,
                    source_note_updated_at=source.updated_at,
                    created_at=timestamp,
                )
        except sqlite3.IntegrityError as error:
            if "publications.type_id, publications.slug" in str(error):
                raise DuplicateSlugError((type_id, slug)) from error
            raise PublishingPersistenceError("could not create publication") from error
        events: tuple[PublicationEvent, ...] = (
            PublicationCreated(
                publication_id=publication["id"],
                note_id=source.note_id,
                type_id=provider.type_id,
                slug=canonical_slug,
                occurred_at=timestamp,
            ),
            PublicationVersionCreated(
                publication_id=publication["id"],
                version=1,
                source_note_id=source.note_id,
                occurred_at=timestamp,
            ),
        )
        self._dispatch(events)
        return publication

    def save_draft_from_note(
        self,
        publication_id: int,
        *,
        summary: str | None = None,
    ) -> dict[str, Any]:
        # The publication determines its immutable source Note; callers cannot
        # replace note_id after creation.
        timestamp = self._timestamp()
        events: tuple[PublicationEvent, ...]
        try:
            with database_connection(self.database_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                publication = self._require_publication(connection, publication_id)
                self._require_provider(publication["type_id"], enabled=True)
                if publication["status"] == "archived":
                    raise InvalidPublicationTransitionError(
                        "restore an archived publication before saving a draft"
                    )
                source = self._require_source_note(publication["note_id"])
                next_version = publication["latest_version"] + 1
                draft_summary = publication["summary"] if summary is None else summary
                version = repository.create_publication_version(
                    connection,
                    publication_id,
                    version=next_version,
                    title=source.title,
                    summary=draft_summary,
                    markdown=source.markdown,
                    source_note_updated_at=source.updated_at,
                    created_at=timestamp,
                )
                status = "published" if publication["published_version"] else "draft"
                updated = repository.update_publication_projection_and_pointers(
                    connection,
                    publication_id,
                    title=version["title"],
                    summary=version["summary"],
                    status=status,
                    latest_version=next_version,
                    published_version=publication["published_version"],
                    draft_version=next_version,
                    published_at=publication["published_at"],
                    archived_at=None,
                    updated_at=timestamp,
                )
                events = (
                    PublicationVersionCreated(
                        publication_id=publication_id,
                        version=next_version,
                        source_note_id=source.note_id,
                        occurred_at=timestamp,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise PublishingPersistenceError("could not save publication draft") from error
        self._dispatch(events)
        return updated  # type: ignore[return-value]

    def publish(self, publication_id: int) -> dict[str, Any]:
        timestamp = self._timestamp()
        with database_connection(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication = self._require_publication(connection, publication_id)
            provider = self._require_provider(publication["type_id"], enabled=True)
            if publication["status"] == "archived":
                raise InvalidPublicationTransitionError(
                    "restore an archived publication before publishing"
                )
            draft_version = publication["draft_version"]
            if draft_version is None:
                raise InvalidPublicationTransitionError(
                    "publication has no unpublished draft"
                )
            version = self._require_version(connection, publication_id, draft_version)
            issues = tuple(
                provider.validate_publish(
                    PublicationSnapshot(
                        title=version["title"],
                        summary=version["summary"],
                        markdown=version["markdown"],
                    )
                )
            )
            if issues:
                raise PublicationValidationError(issues)
            updated = repository.update_publication_projection_and_pointers(
                connection,
                publication_id,
                title=version["title"],
                summary=version["summary"],
                status="published",
                latest_version=publication["latest_version"],
                published_version=draft_version,
                draft_version=None,
                published_at=timestamp,
                archived_at=None,
                updated_at=timestamp,
            )
        self._dispatch(
            (
                PublicationPublished(
                    publication_id=publication_id,
                    version=draft_version,
                    occurred_at=timestamp,
                ),
            )
        )
        return updated  # type: ignore[return-value]

    def discard_draft(self, publication_id: int) -> dict[str, Any]:
        timestamp = self._timestamp()
        with database_connection(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication = self._require_publication(connection, publication_id)
            draft_version = publication["draft_version"]
            if draft_version is None:
                raise InvalidPublicationTransitionError(
                    "publication has no draft to discard"
                )
            if publication["published_version"] is None:
                updated = repository.update_publication_projection_and_pointers(
                    connection,
                    publication_id,
                    title=publication["title"],
                    summary=publication["summary"],
                    status="archived",
                    latest_version=publication["latest_version"],
                    published_version=None,
                    draft_version=None,
                    published_at=None,
                    archived_at=timestamp,
                    updated_at=timestamp,
                )
                events: tuple[PublicationEvent, ...] = (
                    PublicationDraftDiscarded(
                        publication_id=publication_id,
                        discarded_version=draft_version,
                        occurred_at=timestamp,
                    ),
                    PublicationArchived(
                        publication_id=publication_id,
                        occurred_at=timestamp,
                    ),
                )
            else:
                version = self._require_version(
                    connection, publication_id, publication["published_version"]
                )
                updated = repository.update_publication_projection_and_pointers(
                    connection,
                    publication_id,
                    title=version["title"],
                    summary=version["summary"],
                    status="published",
                    latest_version=publication["latest_version"],
                    published_version=publication["published_version"],
                    draft_version=None,
                    published_at=publication["published_at"],
                    archived_at=None,
                    updated_at=timestamp,
                )
                events = (
                    PublicationDraftDiscarded(
                        publication_id=publication_id,
                        discarded_version=draft_version,
                        occurred_at=timestamp,
                    ),
                )
        self._dispatch(events)
        return updated  # type: ignore[return-value]

    def archive(self, publication_id: int) -> dict[str, Any]:
        timestamp = self._timestamp()
        with database_connection(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication = self._require_publication(connection, publication_id)
            if publication["status"] == "archived":
                raise InvalidPublicationTransitionError("publication is already archived")
            projection = repository.get_projection_version(connection, publication)
            if projection is None:
                raise PublishingPersistenceError("publication projection is missing")
            updated = repository.update_publication_projection_and_pointers(
                connection,
                publication_id,
                title=projection["title"],
                summary=projection["summary"],
                status="archived",
                latest_version=publication["latest_version"],
                published_version=publication["published_version"],
                draft_version=publication["draft_version"],
                published_at=publication["published_at"],
                archived_at=timestamp,
                updated_at=timestamp,
            )
        self._dispatch(
            (PublicationArchived(publication_id=publication_id, occurred_at=timestamp),)
        )
        return updated  # type: ignore[return-value]

    def restore(self, publication_id: int) -> dict[str, Any]:
        timestamp = self._timestamp()
        with database_connection(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            publication = self._require_publication(connection, publication_id)
            if publication["status"] != "archived":
                raise InvalidPublicationTransitionError("publication is not archived")
            projection = repository.get_projection_version(connection, publication)
            if projection is None:
                raise PublishingPersistenceError("publication projection is missing")
            published_version = publication["published_version"]
            draft_version = publication["draft_version"]
            latest_version = publication["latest_version"]
            events: tuple[PublicationEvent, ...]
            if published_version is None and draft_version is None:
                # A discarded, never-published draft keeps immutable history but
                # no longer points at the discarded working version. Restoring
                # copies the last snapshot into a new working version.
                latest_version += 1
                projection = repository.create_publication_version(
                    connection,
                    publication_id,
                    version=latest_version,
                    title=projection["title"],
                    summary=projection["summary"],
                    markdown=projection["markdown"],
                    source_note_updated_at=projection["source_note_updated_at"],
                    created_at=timestamp,
                )
                draft_version = latest_version
                events = (
                    PublicationVersionCreated(
                        publication_id=publication_id,
                        version=latest_version,
                        source_note_id=publication["note_id"],
                        occurred_at=timestamp,
                    ),
                    PublicationRestored(
                        publication_id=publication_id,
                        status="draft",
                        occurred_at=timestamp,
                    ),
                )
            else:
                events = (
                    PublicationRestored(
                        publication_id=publication_id,
                        status="published" if published_version else "draft",
                        occurred_at=timestamp,
                    ),
                )
            status = "published" if published_version else "draft"
            updated = repository.update_publication_projection_and_pointers(
                connection,
                publication_id,
                title=projection["title"],
                summary=projection["summary"],
                status=status,
                latest_version=latest_version,
                published_version=published_version,
                draft_version=draft_version,
                published_at=publication["published_at"],
                archived_at=None,
                updated_at=timestamp,
            )
        self._dispatch(events)
        return updated  # type: ignore[return-value]

    def restore_version_as_draft(
        self,
        publication_id: int,
        version: int,
    ) -> dict[str, Any]:
        timestamp = self._timestamp()
        try:
            with database_connection(self.database_path) as connection:
                connection.execute("BEGIN IMMEDIATE")
                publication = self._require_publication(connection, publication_id)
                self._require_provider(publication["type_id"], enabled=True)
                if publication["status"] == "archived":
                    raise InvalidPublicationTransitionError(
                        "restore an archived publication before restoring a version"
                    )
                source_version = self._require_version(
                    connection, publication_id, version
                )
                next_version = publication["latest_version"] + 1
                restored = repository.create_publication_version(
                    connection,
                    publication_id,
                    version=next_version,
                    title=source_version["title"],
                    summary=source_version["summary"],
                    markdown=source_version["markdown"],
                    source_note_updated_at=source_version["source_note_updated_at"],
                    created_at=timestamp,
                )
                status = "published" if publication["published_version"] else "draft"
                updated = repository.update_publication_projection_and_pointers(
                    connection,
                    publication_id,
                    title=restored["title"],
                    summary=restored["summary"],
                    status=status,
                    latest_version=next_version,
                    published_version=publication["published_version"],
                    draft_version=next_version,
                    published_at=publication["published_at"],
                    archived_at=None,
                    updated_at=timestamp,
                )
        except sqlite3.IntegrityError as error:
            raise PublishingPersistenceError("could not restore publication version") from error
        self._dispatch(
            (
                PublicationVersionCreated(
                    publication_id=publication_id,
                    version=next_version,
                    source_note_id=publication["note_id"],
                    occurred_at=timestamp,
                ),
            )
        )
        return updated  # type: ignore[return-value]

    def get_public_content(
        self,
        *,
        publication_id: int | None = None,
        type_id: str | None = None,
        slug: str | None = None,
    ) -> PublicPublicationContent:
        with database_connection(self.database_path) as connection:
            if publication_id is not None:
                publication = repository.get_publication(connection, publication_id)
            elif type_id is not None and slug is not None:
                publication = repository.get_publication_by_slug(
                    connection, type_id=type_id, slug=slug
                )
            else:
                raise ValueError("publication_id or type_id and slug are required")
            if publication is None:
                raise PublicationNotFoundError(publication_id or (type_id, slug))
            version = repository.get_public_version(connection, publication)
            if version is None:
                raise PublicationNotFoundError(publication_id or (type_id, slug))
            return PublicPublicationContent.from_rows(publication, version)


__all__ = (
    "DuplicateSlugError",
    "InvalidPublicationTransitionError",
    "InvalidSlugError",
    "PublicationNotFoundError",
    "PublicationTypeDisabledError",
    "PublicationTypeNotFoundError",
    "PublicationValidationError",
    "PublicationVersionNotFoundError",
    "PublishingError",
    "PublishingPersistenceError",
    "PublishingService",
    "SourceNoteNotFoundError",
)
