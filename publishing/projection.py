from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

from publishing.database import database_connection
from publishing.registry import PublicationTypeRegistry


def build_portal_projection(
    database_path: str | Path,
    registry: PublicationTypeRegistry,
    *,
    limit_per_type: int = 3,
    is_workspace_publicly_available: Callable[[str], bool] | None = None,
) -> MappingProxyType:
    """Build a read-only, query-on-read view of current public snapshots."""

    if not 1 <= limit_per_type <= 20:
        raise ValueError("limit_per_type must be between 1 and 20")
    groups: list[dict[str, Any]] = []
    recent: list[dict[str, Any]] = []
    with database_connection(database_path) as connection:
        publication_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(publications)")
        }
        has_public_id = "public_id" in publication_columns
        for provider in registry.get_all():
            rows = connection.execute(
                f"""
                SELECT p.id, {"p.public_id, p.workspace_id," if has_public_id else ""} p.type_id, p.slug, p.published_at,
                       v.version, v.title, v.summary
                FROM publications AS p
                JOIN publication_versions AS v
                  ON v.publication_id = p.id
                 AND v.version = p.published_version
                WHERE p.status = 'published' AND p.type_id = ?
                ORDER BY p.published_at DESC, p.id DESC
                LIMIT ?
                """,
                (provider.type_id, limit_per_type),
            ).fetchall()
            visible_rows = (
                [
                    row
                    for row in rows
                    if is_workspace_publicly_available is None
                    or not has_public_id
                    or is_workspace_publicly_available(row["workspace_id"])
                ]
                if has_public_id
                else rows
            )
            items = tuple(
                MappingProxyType(
                    {
                        **dict(row),
                        "href": (
                            f"/p/{row['public_id']}/{row['slug']}"
                            if has_public_id
                            else f"{provider.route_prefix}/{row['slug']}"
                        ),
                    }
                )
                for row in visible_rows
            )
            groups.append(
                {
                    "type_id": provider.type_id,
                    "display_name": provider.display_name,
                    "items": items,
                }
            )
            recent.extend(dict(item) for item in items)
    recent.sort(
        key=lambda item: (item["published_at"] or "", item["id"]),
        reverse=True,
    )
    return MappingProxyType(
        {
            "groups": tuple(MappingProxyType(group) for group in groups),
            "recent": tuple(MappingProxyType(item) for item in recent),
        }
    )


__all__ = ("build_portal_projection",)
