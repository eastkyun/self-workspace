from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from apps.notes.app.schemas import NoteCreate
from portal.main import create_app
from publishing.database import database_connection, initialize_database
from publishing.internal_link_provider import PublicationInternalLinkProvider
from publishing.registry import create_default_registry
from publishing import repository


ROOT = Path(__file__).resolve().parents[2]


def type_registry():
    return create_default_registry(
        icon_root=ROOT / "portal" / "static",
        template_root=ROOT / "publishing" / "templates",
    )


def seed_publication(
    database_path: Path,
    *,
    type_id: str,
    slug: str,
    status: str,
) -> None:
    with database_connection(database_path) as connection:
        publication = repository.create_publication(
            connection,
            note_id=1,
            type_id=type_id,
            slug=slug,
            title="Published title",
            summary="",
            markdown="# Published",
            source_note_updated_at="2026-07-30T00:00:00Z",
            created_at="2026-07-30T00:00:00Z",
        )
        repository.create_publication_version(
            connection,
            publication["id"],
            version=2,
            title="Working draft title",
            summary="",
            markdown="# Draft",
            source_note_updated_at="2026-07-30T01:00:00Z",
            created_at="2026-07-30T01:00:00Z",
        )
        repository.update_publication_projection_and_pointers(
            connection,
            publication["id"],
            title="Working draft title",
            summary="",
            status=status,
            latest_version=2,
            published_version=1,
            draft_version=2,
            published_at="2026-07-30T00:30:00Z",
            archived_at=(
                "2026-07-30T02:00:00Z" if status == "archived" else None
            ),
            updated_at="2026-07-30T02:00:00Z",
        )


def test_provider_resolves_only_current_public_snapshot(tmp_path: Path) -> None:
    database_path = initialize_database(tmp_path / "publishing.db")
    seed_publication(
        database_path,
        type_id="manual",
        slug="install-guide",
        status="published",
    )
    seed_publication(
        database_path,
        type_id="manual",
        slug="archived-guide",
        status="archived",
    )

    provider = PublicationInternalLinkProvider(
        database_path,
        type_id="manual",
        registry=type_registry(),
    )
    results = provider.resolve_many(
        ("install-guide", "archived-guide", "../secret", "Install-Guide")
    )

    assert tuple(results) == ("install-guide",)
    assert results["install-guide"].href == "/manual/install-guide"
    assert results["install-guide"].label == "Published title"


def test_provider_never_resolves_a_different_publication_type(
    tmp_path: Path,
) -> None:
    database_path = initialize_database(tmp_path / "publishing.db")
    seed_publication(
        database_path,
        type_id="wiki",
        slug="install-guide",
        status="published",
    )
    provider = PublicationInternalLinkProvider(
        database_path,
        type_id="manual",
        registry=type_registry(),
    )

    assert provider.resolve_many(("install-guide",)) == {}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_portal_resolver_composes_registered_publication_routes(
    tmp_path: Path,
) -> None:
    application = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
        publishing_database_path=tmp_path / "publishing.db",
    )
    async with application.router.lifespan_context(application):
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as client:
            note = application.state.legacy_notes_service.create_note(
                application.state.legacy_notes_context,
                NoteCreate(
                    title="Workspace guide",
                    content="# Guide",
                    note_type="general",
                    is_pinned=False,
                )
            )
            publication = (
                await client.post(
                    "/api/publications",
                    json={
                        "note_id": note["id"],
                        "type_id": "manual",
                        "slug": "workspace-guide",
                        "summary": "",
                    },
                )
            ).json()
            draft_resolution = await client.post(
                "/api/internal-links/resolve",
                json={"links": [{"type": "manual", "target": "workspace-guide"}]},
            )
            await client.post(f"/api/publications/{publication['id']}/publish")
            published_resolution = await client.post(
                "/api/internal-links/resolve",
                json={"links": [{"type": "manual", "target": "workspace-guide"}]},
            )

    assert draft_resolution.status_code == 200
    assert draft_resolution.json()[0]["exists"] is False
    assert published_resolution.json() == [
        {
            "type": "manual",
            "target": "workspace-guide",
            "exists": True,
            "href": f"/p/{publication['public_id']}/workspace-guide",
            "label": "Workspace guide",
        }
    ]
