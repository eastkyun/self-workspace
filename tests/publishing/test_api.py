from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from apps.notes.app.schemas import NoteCreate, NoteUpdate
from portal.main import create_app
from publishing.database import verify_workspace_database

pytestmark = pytest.mark.anyio


@pytest.fixture
def application(tmp_path: Path):
    return create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
        publishing_database_path=tmp_path / "publishing.db",
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(application):
    async with application.router.lifespan_context(application):
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://testserver"
        ) as active:
            active.workspace_app = application
            yield active


async def create_note(client: AsyncClient, *, content: str = "# Install\n\nSafe") -> dict:
    return client.workspace_app.state.legacy_notes_service.create_note(
        client.workspace_app.state.legacy_notes_context,
        NoteCreate(
            title="Install guide",
            content=content,
            note_type="general",
            is_pinned=False,
        )
    )


async def create_publication(
    client: AsyncClient,
    note_id: int,
    *,
    type_id: str = "manual",
    slug: str = "install-guide",
) -> dict:
    response = await client.post(
        "/api/publications",
        json={
            "note_id": note_id,
            "type_id": type_id,
            "slug": slug,
            "summary": "Setup reference",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_temp_database_injection_and_type_registry_are_explicit(
    tmp_path: Path,
) -> None:
    application = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
    )

    async with application.router.lifespan_context(application):
        async with AsyncClient(transport=ASGITransport(app=application), base_url="http://testserver") as client:
            response = await client.get("/api/publication-types")

    assert application.state.publishing_database_path == tmp_path / "publishing.db"
    assert application.state.publishing_database_path.is_file()
    verify_workspace_database(application.state.publishing_database_path)
    assert [item["type_id"] for item in response.json()] == ["manual", "wiki"]
    assert response.json()[0]["render_options"]["toc_enabled"] is True
    assert application.state.publishing_service.registry is (
        application.state.publication_type_registry
    )


async def test_preview_reads_saved_note_without_writing_publication(
    client: AsyncClient,
) -> None:
    note = await create_note(client)

    preview = await client.post(
        "/api/publications/preview-from-note",
        json={
            "note_id": note["id"],
            "type_id": "manual",
            "title": "Unsaved title must not win",
            "summary": "Preview",
        },
    )

    assert preview.status_code == 200
    assert preview.json()["title"] == "Install guide"
    assert preview.json()["markdown"] == "# Install\n\nSafe"
    assert preview.json()["render_options"]["showToc"] is True
    assert (await client.get("/api/publications")).json() == []


async def test_publication_lifecycle_versions_and_public_visibility(
    client: AsyncClient,
) -> None:
    note = await create_note(client)
    publication = await create_publication(client, note["id"])
    publication_id = publication["id"]

    assert (await client.get(f"/manual/{publication['slug']}")).status_code == 404
    assert (await client.get(f"/api/publications/{publication_id}")).json()["version"][
        "markdown"
    ] == "# Install\n\nSafe"
    published = await client.post(f"/api/publications/{publication_id}/publish")
    assert published.status_code == 200
    assert published.json()["published_version"] == 1

    public = await client.get("/manual/install-guide")
    assert public.status_code == 200
    assert "Install guide" in public.text
    assert "# Install" in public.text
    assert 'href="/static/shared/shell.css"' in public.text
    assert 'src="/static/shared/shell.js"' in public.text
    assert "/manual/static/" not in public.text
    assert "/static/publishing/publication-viewer.js" in public.text
    assert 'id="publication-delete"' in public.text
    assert (
        f"/api/v1/workspaces/{publication['workspace_id']}/publications/"
        f"{publication_id}/archive"
        in public.text
    )
    assert Path("publishing/static/publication-viewer.js").is_file()
    assert "install-guide" in (await client.get("/publications/")).text

    client.workspace_app.state.legacy_notes_service.update_note(
        client.workspace_app.state.legacy_notes_context,
        note["id"],
        NoteUpdate(
            title="Install guide v2",
            content="## Updated",
            note_type="general",
            is_pinned=False,
        ),
    )
    draft = await client.post(
        f"/api/publications/{publication_id}/drafts/from-note",
        json={"summary": "Second"},
    )
    assert draft.status_code == 200
    assert draft.json()["latest_version"] == 2
    assert draft.json()["published_version"] == 1
    assert (
        await client.get("/manual/install-guide")
    ).text.find("Install guide v2") == -1
    versions = (await client.get(
        f"/api/publications/{publication_id}/versions"
    )).json()
    assert [item["version"] for item in versions] == [2, 1]

    assert (
        await client.delete(f"/api/publications/{publication_id}/draft")
    ).status_code == 200
    assert (
        await client.post(f"/api/publications/{publication_id}/archive")
    ).status_code == 200
    assert (await client.get("/manual/install-guide")).status_code == 404
    assert (
        await client.post(f"/api/publications/{publication_id}/restore")
    ).status_code == 200
    restored = await client.post(
        f"/api/publications/{publication_id}/versions/1/restore-as-draft"
    )
    assert restored.status_code == 200
    assert restored.json()["draft_version"] == 3


async def test_wiki_view_uses_safe_json_source_and_does_not_render_server_html(
    client: AsyncClient,
) -> None:
    malicious = '</script><script id="executed">alert(1)</script>\n\n**safe**'
    note = await create_note(client, content=malicious)
    publication = await create_publication(
        client, note["id"], type_id="wiki", slug="safe-source"
    )
    await client.post(f"/api/publications/{publication['id']}/publish")

    response = await client.get("/wiki/safe-source")

    assert response.status_code == 200
    assert '<script id="executed">' not in response.text
    assert "\\u003c/script\\u003e" in response.text
    source = response.text.split(
        '<script id="publication-source" type="application/json">', 1
    )[1].split("</script>", 1)[0]
    assert json.loads(source)["markdown"] == malicious
    assert '<strong>safe</strong>' not in response.text


async def test_api_errors_do_not_expose_database_constraints(client: AsyncClient) -> None:
    note = await create_note(client)
    publication = await create_publication(client, note["id"])

    duplicate = await client.post(
        "/api/publications",
        json={
            "note_id": note["id"],
            "type_id": "manual",
            "slug": "install-guide",
        },
    )
    assert duplicate.status_code == 409
    assert "UNIQUE constraint" not in duplicate.text
    assert (await client.post(
        "/api/publications",
        json={"note_id": note["id"], "type_id": "manual", "slug": "Bad Slug"},
    )).status_code == 422
    assert (await client.post(
        f"/api/publications/{publication['id']}/publish"
    )).status_code == 200
    assert (await client.post(
        f"/api/publications/{publication['id']}/publish"
    )).status_code == 409
    assert (await client.get("/api/publications/not-an-int")).status_code == 422
    assert (await client.get("/api/publications/999")).status_code == 404


async def test_public_search_and_library_use_published_snapshot_without_hub_projection(
    client: AsyncClient,
) -> None:
    note = await create_note(client)
    publication = await create_publication(client, note["id"])
    await client.post(f"/api/publications/{publication['id']}/publish")

    search = await client.get("/api/publications/search", params={"q": "Install"})
    home = await client.get("/")
    library = await client.get("/publications/")

    assert search.status_code == 200
    assert [(item["slug"], item["version"]) for item in search.json()] == [
        ("install-guide", 1)
    ]
    assert home.status_code == 200
    assert "지식 발행물" not in home.text
    canonical_href = f"/p/{publication['public_id']}/install-guide"
    assert library.status_code == 200
    assert f'href="{canonical_href}"' in library.text
    assert "Install guide" in library.text
    assert "발행된 지식을 한곳에서 읽으세요" in library.text
    assert 'class="publication-home-stats"' in library.text
    assert "Manual" in library.text
    assert "v1" in library.text

    links = await client.post(
        "/api/internal-links/resolve",
        json={"links": [{"type": "manual", "target": "install-guide"}]},
    )
    assert links.status_code == 200
    assert links.json() == [
        {
            "type": "manual",
            "target": "install-guide",
            "exists": True,
            "href": canonical_href,
            "label": "Install guide",
        }
    ]
