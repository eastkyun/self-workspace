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
class ManualPublicationType:
    type_id: str = "manual"
    display_name: str = "Manual"
    icon: str = "/static/icons/manual.svg"
    order: int = 10
    enabled: bool = True
    route_prefix: str = "/manual"
    template_name: str = "publication_viewer.html"
    slug_policy: SlugPolicy = field(default_factory=SlugPolicy)

    def render_options(self) -> RenderOptions:
        return RenderOptions(
            toc_enabled=True,
            toc_levels=(2, 3, 4),
            related_enabled=False,
            version_visible=True,
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
