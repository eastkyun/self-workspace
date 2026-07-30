from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from publishing.registry import PublicationTypeRegistry
from shared.internal_links import InternalLinkProvider, ProviderLink


class PublicationInternalLinkProvider(InternalLinkProvider):
    """Resolve one registered publication type from its current public snapshot."""

    def __init__(
        self,
        database_path: Path,
        *,
        type_id: str,
        registry: PublicationTypeRegistry,
    ) -> None:
        self.database_path = database_path
        self.type_id = type_id
        self._publication_type = registry.require(type_id)
        self._slug_pattern = re.compile(self._publication_type.slug_policy.pattern)

    def resolve_many(self, targets: tuple[str, ...]) -> dict[str, ProviderLink]:
        slugs = tuple(
            target
            for target in dict.fromkeys(targets)
            if (
                self._publication_type.slug_policy.min_length
                <= len(target)
                <= self._publication_type.slug_policy.max_length
                and self._slug_pattern.fullmatch(target)
                and target not in self._publication_type.slug_policy.reserved_words
            )
        )
        if not slugs:
            return {}

        placeholders = ", ".join("?" for _ in slugs)
        database_uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as connection:
            rows = connection.execute(
                f"""
                SELECT p.slug, v.title
                FROM publications AS p
                JOIN publication_versions AS v
                  ON v.publication_id = p.id
                 AND v.version = p.published_version
                WHERE p.type_id = ?
                  AND p.status = 'published'
                  AND p.slug IN ({placeholders})
                """,
                (self.type_id, *slugs),
            ).fetchall()

        prefix = self._publication_type.route_prefix
        return {
            slug: ProviderLink(
                target=slug,
                href=f"{prefix}/{slug}",
                label=title,
            )
            for slug, title in rows
        }


def create_publication_internal_link_providers(
    database_path: Path,
    registry: PublicationTypeRegistry,
) -> dict[str, PublicationInternalLinkProvider]:
    return {
        provider.type_id: PublicationInternalLinkProvider(
            database_path,
            type_id=provider.type_id,
            registry=registry,
        )
        for provider in registry.get_all()
    }


__all__ = [
    "PublicationInternalLinkProvider",
    "create_publication_internal_link_providers",
]
