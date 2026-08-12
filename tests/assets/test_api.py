from __future__ import annotations

import io
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from apps.notes.app.schemas import NoteCreate, NoteUpdate
from assets.database import database_connection, verify_workspace_database
from portal.main import create_app
from publishing.database import database_connection as publishing_database_connection

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def application(tmp_path: Path):
    return create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
        publishing_database_path=tmp_path / "publishing.db",
        asset_database_path=tmp_path / "assets.db",
        asset_blob_root=tmp_path / "asset-blobs",
    )


@pytest.fixture
async def client(application):
    async with application.router.lifespan_context(application):
        async with AsyncClient(
            transport=ASGITransport(app=application),
            base_url="http://testserver",
        ) as active:
            active.workspace_app = application
            yield active


def png_bytes(color: tuple[int, int, int] = (20, 40, 60)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), color).save(output, format="PNG")
    return output.getvalue()


async def upload(
    client: AsyncClient,
    *,
    session: str = "upload-session-1",
    content: bytes | None = None,
):
    return await client.post(
        "/api/assets/images",
        params={"upload_session_id": session},
        headers={
            "Content-Type": "image/png",
            "X-File-Name": "%ED%95%9C%EA%B8%80-%ED%99%94%EB%A9%B4.png",
        },
        content=content or png_bytes(),
    )


async def test_asset_paths_are_injected_and_lifespan_verifies_database(
    tmp_path: Path,
) -> None:
    application = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
    )
    assert application.state.asset_database_path == tmp_path / "assets.db"
    assert application.state.asset_blob_root == tmp_path / "blobs"
    assert not application.state.asset_database_path.exists()

    async with application.router.lifespan_context(application):
        verify_workspace_database(application.state.asset_database_path)


async def test_upload_resolve_and_serve_normalized_image(client: AsyncClient) -> None:
    response = await upload(client)

    assert response.status_code == 201
    uploaded = response.json()
    assert uploaded["exists"] is True
    assert uploaded["media_type"] == "image/png"
    assert uploaded["href"] == f"/api/assets/{uploaded['id']}/content"
    assert client.workspace_app.state.asset_service.get_asset(
        uploaded["id"]
    )["original_name"] == "한글-화면.png"

    resolved = await client.post(
        "/api/assets/resolve",
        json={"ids": [uploaded["id"], 999999]},
    )
    assert resolved.status_code == 200
    assert resolved.json() == [
        uploaded,
        {
            "id": 999999,
            "exists": False,
            "href": None,
            "media_type": None,
            "byte_size": None,
        },
    ]

    content = await client.get(uploaded["href"])
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/png"
    assert content.headers["x-content-type-options"] == "nosniff"
    assert "immutable" in content.headers["cache-control"]
    with Image.open(io.BytesIO(content.content)) as image:
        assert image.size == (3, 2)


async def test_upload_reports_size_media_and_validation_errors(
    client: AsyncClient,
) -> None:
    unsupported = await client.post(
        "/api/assets/images",
        params={"upload_session_id": "session"},
        headers={"Content-Type": "image/gif", "X-File-Name": "x.gif"},
        content=b"GIF89a",
    )
    assert unsupported.status_code == 415

    oversized = await client.post(
        "/api/assets/images",
        params={"upload_session_id": "session"},
        headers={
            "Content-Type": "image/png",
            "Content-Length": str(10 * 1024 * 1024 + 1),
        },
        content=b"x",
    )
    assert oversized.status_code == 413

    malformed = await client.post(
        "/api/assets/images",
        params={"upload_session_id": "session"},
        headers={"Content-Type": "image/png"},
        content=b"not-an-image",
    )
    assert malformed.status_code == 415

    invalid_session = await client.post(
        "/api/assets/images",
        params={"upload_session_id": "../escape"},
        headers={"Content-Type": "image/png"},
        content=png_bytes(),
    )
    assert invalid_session.status_code == 422


async def test_resolve_batch_is_bounded_and_payload_is_strict(
    client: AsyncClient,
) -> None:
    too_many = await client.post(
        "/api/assets/resolve",
        json={"ids": list(range(1, 52))},
    )
    assert too_many.status_code == 422
    extra = await client.post(
        "/api/assets/resolve",
        json={"ids": [1], "url": "https://example.com"},
    )
    assert extra.status_code == 422
    invalid_id = await client.post("/api/assets/resolve", json={"ids": [0]})
    assert invalid_id.status_code == 422


async def test_reconcile_reloads_persisted_note_markdown_and_rejects_client_ids(
    client: AsyncClient,
) -> None:
    uploaded = (await upload(client)).json()
    notes_service = client.workspace_app.state.legacy_notes_service
    note = notes_service.create_note(
        client.workspace_app.state.legacy_notes_context,
        NoteCreate(
            title="Image note",
            content="No image yet",
            note_type="general",
            is_pinned=False,
        )
    )
    notes_service.update_note(
        client.workspace_app.state.legacy_notes_context,
        note["id"],
        NoteUpdate(
            title=note["title"],
            content=f"![stored](image://{uploaded['id']})",
            note_type=note["note_type"],
            is_pinned=False,
        ),
    )

    response = await client.post(
        "/api/assets/references/reconcile",
        json={
            "owner_type": "note",
            "owner_id": str(note["id"]),
            "field": "content",
        },
    )
    assert response.status_code == 200
    assert response.json()["asset_ids"] == [uploaded["id"]]

    with database_connection(
        client.workspace_app.state.asset_database_path
    ) as connection:
        reference = connection.execute(
            "SELECT asset_id, owner_type, owner_id, field "
            "FROM asset_references WHERE owner_type = 'note'"
        ).fetchone()
    assert tuple(reference) == (
        uploaded["id"],
        "note",
        str(note["id"]),
        "content",
    )

    client_authority = await client.post(
        "/api/assets/references/reconcile",
        json={
            "owner_type": "note",
            "owner_id": str(note["id"]),
            "field": "content",
            "asset_ids": [999999],
        },
    )
    assert client_authority.status_code == 422


async def test_reconcile_reads_immutable_publication_version_markdown(
    client: AsyncClient,
) -> None:
    uploaded = (await upload(client, session="publication-upload")).json()
    note = client.workspace_app.state.legacy_notes_service.create_note(
        client.workspace_app.state.legacy_notes_context,
        NoteCreate(
            title="Published image",
            content=f"![publication](image://{uploaded['id']})",
            note_type="general",
            is_pinned=False,
        )
    )
    publication = client.workspace_app.state.publishing_service.create_draft_from_note(
        note_id=note["id"],
        type_id="manual",
        slug="published-image",
        summary="",
    )
    with publishing_database_connection(
        client.workspace_app.state.publishing_database_path
    ) as connection:
        version_row = connection.execute(
            "SELECT id FROM publication_versions "
            "WHERE publication_id = ? AND version = ?",
            (publication["id"], publication["draft_version"]),
        ).fetchone()

    response = await client.post(
        "/api/assets/references/reconcile",
        json={
            "owner_type": "publication_version",
            "owner_id": str(version_row["id"]),
            "field": "markdown",
        },
    )
    assert response.status_code == 200
    assert response.json()["asset_ids"] == [uploaded["id"]]

    missing = await client.post(
        "/api/assets/references/reconcile",
        json={
            "owner_type": "publication_version",
            "owner_id": "999999",
            "field": "markdown",
        },
    )
    assert missing.status_code == 404


async def test_publication_create_reconciles_server_stored_markdown(
    client: AsyncClient,
) -> None:
    uploaded = (await upload(client, session="auto-publication")).json()
    note = client.workspace_app.state.legacy_notes_service.create_note(
        client.workspace_app.state.legacy_notes_context,
        NoteCreate(
            title="Auto reconcile",
            content=f"![stored](image://{uploaded['id']})",
            note_type="general",
            is_pinned=False,
        )
    )

    response = await client.post(
        "/api/publications",
        json={
            "note_id": note["id"],
            "type_id": "manual",
            "slug": "auto-reconcile",
            "summary": "",
        },
    )

    assert response.status_code == 201
    with database_connection(
        client.workspace_app.state.asset_database_path
    ) as connection:
        reference = connection.execute(
            "SELECT owner_type, field, asset_id FROM asset_references "
            "WHERE owner_type = 'publication_version'"
        ).fetchone()
    assert tuple(reference) == (
        "publication_version",
        "markdown",
        uploaded["id"],
    )
