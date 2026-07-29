from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class SlugPolicy:
    """Canonical slug rules owned by a publication type."""

    pattern: str = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    min_length: int = 1
    max_length: int = 80
    reserved_words: tuple[str, ...] = (
        "new",
        "edit",
        "api",
        "versions",
        "assets",
        "preview",
        "publish",
        "archive",
        "restore",
        "search",
        "static",
        "admin",
    )


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """Immutable presentation hints; rendering remains a Core responsibility."""

    toc_enabled: bool
    toc_levels: tuple[int, ...] = ()
    related_enabled: bool = False
    version_visible: bool = True
    backlink_enabled: bool = False


@dataclass(frozen=True, slots=True)
class PublicationSnapshot:
    """Immutable input supplied to type-specific publish validation."""

    title: str
    summary: str
    markdown: str


@dataclass(frozen=True, slots=True)
class PublicationView:
    """Read-only publication data exposed to provider view hooks."""

    publication_id: int
    type_id: str
    slug: str
    title: str
    summary: str
    status: str


@dataclass(frozen=True, slots=True)
class VersionView:
    """Read-only version snapshot exposed to provider view hooks."""

    version: int
    title: str
    summary: str
    markdown: str


@runtime_checkable
class PublicationType(Protocol):
    """MVP provider contract.

    Providers describe and validate a publication type. They do not render
    Markdown and must not own database connections or repositories.
    """

    type_id: str
    display_name: str
    icon: str
    order: int
    enabled: bool
    route_prefix: str
    template_name: str
    slug_policy: SlugPolicy

    def render_options(self) -> RenderOptions: ...

    def validate_publish(self, snapshot: PublicationSnapshot) -> tuple[str, ...]: ...

    def build_view_context(
        self,
        publication: PublicationView,
        version: VersionView,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...

    def navigation_provider(self) -> object | None: ...

    def search_provider(self) -> object | None: ...

    def related_content_provider(self) -> object | None: ...
