from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from publishing.contracts import (
    PublicationSnapshot,
    PublicationView,
    RenderOptions,
    SlugPolicy,
    VersionView,
)


@dataclass(frozen=True, slots=True)
class WikiPublicationType:
    type_id: str = "wiki"
    display_name: str = "Wiki"
    icon: str = "/static/icons/wiki.svg"
    order: int = 20
    enabled: bool = True
    route_prefix: str = "/wiki"
    template_name: str = "publication_viewer.html"
    slug_policy: SlugPolicy = field(default_factory=SlugPolicy)

    def render_options(self) -> RenderOptions:
        return RenderOptions(
            toc_enabled=False,
            toc_levels=(2, 3, 4),
            related_enabled=True,
            version_visible=True,
            backlink_enabled=False,
        )

    def validate_publish(self, snapshot: PublicationSnapshot) -> tuple[str, ...]:
        return ()

    def build_view_context(
        self,
        publication: PublicationView,
        version: VersionView,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return MappingProxyType({})

    def navigation_provider(self) -> object | None:
        return None

    def search_provider(self) -> object | None:
        return None

    def related_content_provider(self) -> object | None:
        return None
