from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol, TypeAlias, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PublicationCreated:
    publication_id: int
    note_id: int
    type_id: str
    slug: str
    occurred_at: str


@dataclass(frozen=True, slots=True)
class PublicationVersionCreated:
    publication_id: int
    version: int
    source_note_id: int
    occurred_at: str


@dataclass(frozen=True, slots=True)
class PublicationPublished:
    publication_id: int
    version: int
    occurred_at: str


@dataclass(frozen=True, slots=True)
class PublicationDraftDiscarded:
    publication_id: int
    discarded_version: int
    occurred_at: str


@dataclass(frozen=True, slots=True)
class PublicationArchived:
    publication_id: int
    occurred_at: str


@dataclass(frozen=True, slots=True)
class PublicationRestored:
    publication_id: int
    status: str
    occurred_at: str


PublicationEvent: TypeAlias = (
    PublicationCreated
    | PublicationVersionCreated
    | PublicationPublished
    | PublicationDraftDiscarded
    | PublicationArchived
    | PublicationRestored
)


@runtime_checkable
class EventSubscriber(Protocol):
    def __call__(self, event: PublicationEvent) -> None: ...


class AfterCommitEventDispatcher:
    """Best-effort in-process events; subscriber failures never undo a commit."""

    def __init__(
        self,
        subscribers: Iterable[EventSubscriber] = (),
    ) -> None:
        self._subscribers = tuple(subscribers)

    def dispatch(self, events: Iterable[PublicationEvent]) -> None:
        for event in events:
            for subscriber in self._subscribers:
                try:
                    subscriber(event)
                except Exception:
                    logger.exception(
                        "Publishing after-commit subscriber failed",
                        extra={"event_type": type(event).__name__},
                    )


__all__ = (
    "AfterCommitEventDispatcher",
    "EventSubscriber",
    "PublicationArchived",
    "PublicationCreated",
    "PublicationDraftDiscarded",
    "PublicationEvent",
    "PublicationPublished",
    "PublicationRestored",
    "PublicationVersionCreated",
)
