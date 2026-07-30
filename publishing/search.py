from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from publishing.database import database_connection

_SUPPORTED_STATUS_SCOPES = frozenset(
    {"public", "all", "draft", "published", "archived"}
)


@dataclass(frozen=True, slots=True)
class PublicationSearchResult:
    """Immutable search projection from one selected version snapshot."""

    publication_id: int
    note_id: int
    type_id: str
    slug: str
    status: str
    version: int
    title: str
    summary: str
    markdown: str
    published_at: str | None
    updated_at: str
    match_rank: int


def _normalize_query(query: str) -> str:
    if not isinstance(query, str):
        raise TypeError("query must be a string")
    return re.sub(r"\s+", " ", query.strip())


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _scope_sql(status_scope: str) -> tuple[str, str]:
    if status_scope == "public":
        return (
            "p.published_version",
            "p.status = 'published' AND p.published_version IS NOT NULL",
        )
    if status_scope == "all":
        return (
            "COALESCE(p.draft_version, p.published_version, p.latest_version)",
            "1 = 1",
        )
    if status_scope == "draft":
        return ("p.draft_version", "p.draft_version IS NOT NULL")
    if status_scope == "published":
        return (
            "p.published_version",
            "p.status = 'published' AND p.published_version IS NOT NULL",
        )
    return (
        "COALESCE(p.draft_version, p.published_version, p.latest_version)",
        "p.status = 'archived'",
    )


class SqliteLikePublicationSearchProvider:
    """Replaceable MVP search backed by parameterized SQLite LIKE queries."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def search(
        self,
        query: str,
        *,
        type_id: str | None = None,
        status_scope: str = "public",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[PublicationSearchResult, ...]:
        normalized = _normalize_query(query)
        if (
            not isinstance(status_scope, str)
            or status_scope not in _SUPPORTED_STATUS_SCOPES
        ):
            raise ValueError(
                "status_scope must be one of: "
                + ", ".join(sorted(_SUPPORTED_STATUS_SCOPES))
            )
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be a non-negative integer")
        if type_id is not None and (not isinstance(type_id, str) or not type_id):
            raise ValueError("type_id must be a non-empty string")

        version_pointer, scope_predicate = _scope_sql(status_scope)
        predicates = [scope_predicate]
        predicate_parameters: list[object] = []
        rank_parameters: list[object] = []
        if type_id is not None:
            predicates.append("p.type_id = ?")
            predicate_parameters.append(type_id)

        rank_sql = "0"
        if normalized:
            escaped = _escape_like(normalized.lower())
            contains = f"%{escaped}%"
            prefix = f"{escaped}%"
            predicates.append(
                """
                (
                    LOWER(p.slug) = ?
                    OR LOWER(v.title) LIKE ? ESCAPE '\\'
                    OR LOWER(v.summary) LIKE ? ESCAPE '\\'
                    OR LOWER(v.markdown) LIKE ? ESCAPE '\\'
                )
                """
            )
            predicate_parameters.extend(
                (normalized.lower(), contains, contains, contains)
            )
            rank_sql = """
                CASE
                    WHEN LOWER(p.slug) = ? THEN 0
                    WHEN LOWER(v.title) = ? THEN 1
                    WHEN LOWER(v.title) LIKE ? ESCAPE '\\' THEN 2
                    WHEN LOWER(v.title) LIKE ? ESCAPE '\\' THEN 3
                    WHEN LOWER(v.summary) LIKE ? ESCAPE '\\' THEN 4
                    WHEN LOWER(v.markdown) LIKE ? ESCAPE '\\' THEN 5
                    ELSE 6
                END
            """
            rank_parameters.extend(
                (
                    normalized.lower(),
                    normalized.lower(),
                    prefix,
                    contains,
                    contains,
                    contains,
                )
            )

        parameters = [
            *rank_parameters,
            *predicate_parameters,
            limit,
            offset,
        ]
        with database_connection(self.database_path) as connection:
            rows = connection.execute(
                f"""
                SELECT
                    p.id AS publication_id,
                    p.note_id,
                    p.type_id,
                    p.slug,
                    p.status,
                    v.version,
                    v.title,
                    v.summary,
                    v.markdown,
                    p.published_at,
                    p.updated_at,
                    {rank_sql} AS match_rank
                FROM publications AS p
                JOIN publication_versions AS v
                  ON v.publication_id = p.id
                 AND v.version = {version_pointer}
                WHERE {' AND '.join(predicates)}
                ORDER BY
                    match_rank ASC,
                    p.published_at DESC,
                    p.id DESC
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return tuple(PublicationSearchResult(**dict(row)) for row in rows)
