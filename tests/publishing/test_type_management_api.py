from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from apps.notes.app.schemas import NoteCreate
from portal.main import create_app
from publishing.types import default_publication_types

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "database_path": tmp_path / "calendar.db",
        "notes_database_path": tmp_path / "notes.db",
        "publishing_database_path": tmp_path / "publishing.db",
        "asset_database_path": tmp_path / "assets.db",
        "asset_blob_root": tmp_path / "blobs",
        "publication_types_path": tmp_path / "types.json",
    }


async def client_for(application):
    return AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://testserver",
    )


def faq_payload(**overrides):
    payload = {
        "type_id": "faq",
        "display_name": "FAQ",
        "order": 30,
        "enabled": True,
        "toc_enabled": False,
        "related_enabled": True,
        "version_visible": True,
    }
    payload.update(overrides)
    return payload


async def test_type_management_crud_protects_builtins_and_requires_restart(
    tmp_path: Path,
) -> None:
    app = create_app(**paths(tmp_path))
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            page = await client.get("/publications/types/")
            empty_home = await client.get("/publications/")
            initial = await client.get("/api/publication-type-manifests")
            created = await client.post(
                "/api/publication-type-manifests",
                json=faq_payload(),
            )
            listed = await client.get("/api/publication-type-manifests")
            active_types = await client.get("/api/publication-types")
            duplicate = await client.post(
                "/api/publication-type-manifests",
                json=faq_payload(),
            )
            order_collision = await client.post(
                "/api/publication-type-manifests",
                json=faq_payload(type_id="glossary", order=30),
            )
            protected = await client.delete(
                "/api/publication-type-manifests/manual"
            )
            updated = await client.put(
                "/api/publication-type-manifests/faq",
                json=faq_payload(display_name="Frequently Asked Questions", order=35),
            )
            removed = await client.delete(
                "/api/publication-type-manifests/faq"
            )
            after_remove = await client.get("/api/publication-type-manifests")

    assert page.status_code == 200
    assert empty_home.status_code == 200
    assert "아직 발행된 문서가 없습니다" in empty_home.text
    assert 'href="/apps/notes/"' in empty_home.text
    assert "Publication Type 관리" in page.text
    assert "/static/publishing/publication-types.js" in page.text
    assert [item["type_id"] for item in initial.json()] == ["manual", "wiki"]
    assert all(item["builtin"] for item in initial.json())
    assert created.status_code == 201
    assert created.json()["restart_required"] is False
    assert [item["type_id"] for item in listed.json()] == [
        "manual",
        "wiki",
        "faq",
    ]
    assert listed.json()[-1]["builtin"] is False
    assert listed.json()[-1]["restart_required"] is False
    assert [item["type_id"] for item in active_types.json()] == [
        "manual",
        "wiki",
        "faq",
    ]
    assert duplicate.status_code == 409
    assert order_collision.status_code == 409
    assert protected.status_code == 409
    assert updated.status_code == 200
    assert updated.json()["display_name"] == "Frequently Asked Questions"
    assert removed.status_code == 200
    assert removed.json() == {
        "type_id": "faq",
        "deleted": True,
        "restart_required": False,
    }
    assert [item["type_id"] for item in after_remove.json()] == ["manual", "wiki"]


async def test_custom_type_applies_immediately_persists_and_cannot_delete_in_use(
    tmp_path: Path,
) -> None:
    app_paths = paths(tmp_path)
    first = create_app(**app_paths)
    async with first.router.lifespan_context(first):
        async with await client_for(first) as client:
            response = await client.post(
                "/api/publication-type-manifests",
                json=faq_payload(),
            )
            assert response.status_code == 201
            assert [
                item["type_id"]
                for item in (await client.get("/api/publication-types")).json()
            ] == ["manual", "wiki", "faq"]

    restarted = create_app(**app_paths)
    async with restarted.router.lifespan_context(restarted):
        async with await client_for(restarted) as client:
            types = await client.get("/api/publication-types")
            manifests = await client.get("/api/publication-type-manifests")
            note = restarted.state.notes_service.create_note(
                NoteCreate(
                    title="Why immutable snapshots?",
                    content="Because published versions must not drift.",
                    note_type="general",
                    is_pinned=False,
                )
            )
            publication = await client.post(
                "/api/publications",
                json={
                    "note_id": note["id"],
                    "type_id": "faq",
                    "slug": "immutable-snapshots",
                    "summary": "Snapshot behavior",
                },
            )
            assert publication.status_code == 201, publication.text
            published = await client.post(
                f"/api/publications/{publication.json()['id']}/publish"
            )
            viewer = await client.get(
                "/publications/view/faq/immutable-snapshots"
            )
            blocked = await client.delete(
                "/api/publication-type-manifests/faq"
            )

    assert [item["type_id"] for item in types.json()] == [
        "manual",
        "wiki",
        "faq",
    ]
    faq = next(item for item in manifests.json() if item["type_id"] == "faq")
    assert faq["builtin"] is False
    assert faq["restart_required"] is False
    assert published.status_code == 200
    assert viewer.status_code == 200
    assert "Why immutable snapshots?" in viewer.text
    assert blocked.status_code == 409
    assert "disable it instead" in blocked.text


async def test_publication_reads_refresh_a_stale_registry_snapshot(
    tmp_path: Path,
) -> None:
    application = create_app(**paths(tmp_path))
    async with application.router.lifespan_context(application):
        async with await client_for(application) as client:
            created = await client.post(
                "/api/publication-type-manifests",
                json=faq_payload(),
            )
            assert created.status_code == 201
            note = application.state.notes_service.create_note(
                NoteCreate(
                    title="Out-of-process manifest update",
                    content="The publication remains readable.",
                    note_type="general",
                    is_pinned=False,
                )
            )
            publication = await client.post(
                "/api/publications",
                json={
                    "note_id": note["id"],
                    "type_id": "faq",
                    "slug": "manifest-refresh",
                    "summary": "Registry refresh regression",
                },
            )
            await client.post(
                f"/api/publications/{publication.json()['id']}/publish"
            )

            application.state.publication_type_registry.replace_providers(
                default_publication_types()
            )
            assert not application.state.publication_type_registry.exists("faq")

            home = await client.get("/publications/")
            viewer = await client.get(
                "/publications/view/faq/manifest-refresh"
            )

    assert home.status_code == 200
    assert "Out-of-process manifest update" in home.text
    assert viewer.status_code == 200
    assert application.state.publication_type_registry.exists("faq")
