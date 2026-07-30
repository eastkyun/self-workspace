from __future__ import annotations

from pathlib import Path

import pytest

from publishing.database import database_connection, initialize_database
from publishing.repository import (
    create_publication,
    create_publication_version,
    update_publication_projection_and_pointers,
)
from publishing.search import (
    PublicationSearchResult,
    SqliteLikePublicationSearchProvider,
)

NOW = "2026-07-30T00:00:00Z"


def _create(
    path: Path,
    *,
    note_id: int,
    type_id: str,
    slug: str,
    title: str,
    summary: str,
    markdown: str,
    timestamp: str,
    publish: bool = True,
) -> dict:
    with database_connection(path) as connection:
        publication = create_publication(
            connection,
            note_id=note_id,
            type_id=type_id,
            slug=slug,
            title=title,
            summary=summary,
            markdown=markdown,
            source_note_updated_at=timestamp,
            created_at=timestamp,
        )
        if publish:
            publication = update_publication_projection_and_pointers(
                connection,
                publication["id"],
                title=title,
                summary=summary,
                status="published",
                latest_version=1,
                published_version=1,
                draft_version=None,
                published_at=timestamp,
                archived_at=None,
                updated_at=timestamp,
            )
    return publication


@pytest.fixture
def search_provider(tmp_path: Path) -> SqliteLikePublicationSearchProvider:
    path = initialize_database(tmp_path / "publishing.db")
    _create(
        path,
        note_id=1,
        type_id="manual",
        slug="install",
        title="Install guide",
        summary="Safe setup instructions",
        markdown="# Install\n\nUse 100% verified steps.",
        timestamp="2026-07-30T01:00:00Z",
    )
    _create(
        path,
        note_id=2,
        type_id="wiki",
        slug="installation-notes",
        title="Installation notes",
        summary="Install field reference",
        markdown="A secondary installation article with item_code.",
        timestamp="2026-07-30T02:00:00Z",
    )
    _create(
        path,
        note_id=3,
        type_id="manual",
        slug="private-install",
        title="Install private draft",
        summary="Not public",
        markdown="Secret install material",
        timestamp="2026-07-30T03:00:00Z",
        publish=False,
    )
    return SqliteLikePublicationSearchProvider(path)


def test_public_search_returns_immutable_ranked_results(
    search_provider: SqliteLikePublicationSearchProvider,
) -> None:
    results = search_provider.search("  install  ")

    assert isinstance(results, tuple)
    assert all(isinstance(item, PublicationSearchResult) for item in results)
    assert [item.slug for item in results] == ["install", "installation-notes"]
    assert [item.match_rank for item in results] == [0, 2]
    with pytest.raises(AttributeError):
        results[0].title = "changed"  # type: ignore[misc]

    exact_title = search_provider.search("Install guide")
    assert [(item.slug, item.match_rank) for item in exact_title] == [("install", 1)]
    collapsed = search_provider.search("Install \n\t guide")
    assert [(item.slug, item.match_rank) for item in collapsed] == [("install", 1)]


def test_rank_falls_back_from_title_to_summary_then_raw_markdown(
    search_provider: SqliteLikePublicationSearchProvider,
) -> None:
    summary_match = search_provider.search("field reference")
    markdown_match = search_provider.search("secondary installation article")

    assert [(item.slug, item.match_rank) for item in summary_match] == [
        ("installation-notes", 4)
    ]
    assert [(item.slug, item.match_rank) for item in markdown_match] == [
        ("installation-notes", 5)
    ]


def test_public_scope_uses_published_snapshot_not_working_draft(
    search_provider: SqliteLikePublicationSearchProvider,
) -> None:
    path = search_provider.database_path
    with database_connection(path) as connection:
        publication = connection.execute(
            "SELECT * FROM publications WHERE slug = 'install'"
        ).fetchone()
        create_publication_version(
            connection,
            publication["id"],
            version=2,
            title="Confidential rewrite",
            summary="",
            markdown="unreleased-only-token",
            source_note_updated_at="2026-07-30T04:00:00Z",
            created_at="2026-07-30T04:00:00Z",
        )
        update_publication_projection_and_pointers(
            connection,
            publication["id"],
            title="Confidential rewrite",
            summary="",
            status="published",
            latest_version=2,
            published_version=1,
            draft_version=2,
            published_at="2026-07-30T01:00:00Z",
            archived_at=None,
            updated_at="2026-07-30T04:00:00Z",
        )

    assert search_provider.search("unreleased-only-token") == ()
    draft_results = search_provider.search(
        "unreleased-only-token", status_scope="draft"
    )
    assert [(item.slug, item.version) for item in draft_results] == [("install", 2)]


def test_public_scope_excludes_draft_and_archived(
    search_provider: SqliteLikePublicationSearchProvider,
) -> None:
    path = search_provider.database_path
    with database_connection(path) as connection:
        archived = connection.execute(
            "SELECT * FROM publications WHERE slug = 'installation-notes'"
        ).fetchone()
        update_publication_projection_and_pointers(
            connection,
            archived["id"],
            title=archived["title"],
            summary=archived["summary"],
            status="archived",
            latest_version=1,
            published_version=1,
            draft_version=None,
            published_at=archived["published_at"],
            archived_at="2026-07-30T04:00:00Z",
            updated_at="2026-07-30T04:00:00Z",
        )

    assert search_provider.search("private") == ()
    assert search_provider.search("installation") == ()
    assert [item.slug for item in search_provider.search(
        "private", status_scope="draft"
    )] == ["private-install"]
    assert [item.slug for item in search_provider.search(
        "installation", status_scope="archived"
    )] == ["installation-notes"]


def test_literal_like_wildcards_are_not_treated_as_patterns(
    search_provider: SqliteLikePublicationSearchProvider,
) -> None:
    assert [item.slug for item in search_provider.search("100%")] == ["install"]
    assert search_provider.search("100_") == ()
    assert [item.slug for item in search_provider.search("item_code")] == [
        "installation-notes"
    ]
    assert search_provider.search("item%code") == ()


def test_type_filter_empty_query_and_pagination_are_deterministic(
    search_provider: SqliteLikePublicationSearchProvider,
) -> None:
    assert [item.slug for item in search_provider.search("")] == [
        "installation-notes",
        "install",
    ]
    assert [item.slug for item in search_provider.search(
        "  \n\t ", type_id="manual"
    )] == ["install"]
    assert [item.slug for item in search_provider.search(
        "", limit=1, offset=1
    )] == ["install"]


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"status_scope": "unknown"}, "status_scope"),
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"offset": -1}, "offset"),
        ({"type_id": ""}, "type_id"),
    ],
)
def test_invalid_search_options_are_rejected(
    search_provider: SqliteLikePublicationSearchProvider,
    kwargs: dict,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        search_provider.search("", **kwargs)
