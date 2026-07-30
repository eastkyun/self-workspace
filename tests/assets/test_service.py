from __future__ import annotations

import io
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from assets.database import database_connection, initialize_database
from assets.repository import list_references
from assets.service import (
    AssetNotFoundError,
    AssetService,
    InvalidImageError,
    MarkdownSourceNotFoundError,
)
from assets.storage import LocalFileStorage

NOW = datetime(2026, 7, 30, tzinfo=UTC)


class StoredMarkdown:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], str] = {}
        self.calls: list[tuple[str, str, str]] = []

    def get_markdown(self, owner_type: str, owner_id: str, field: str) -> str | None:
        key = (owner_type, owner_id, field)
        self.calls.append(key)
        return self.values.get(key)


def encoded_image(format_name: str, *, exif: bool = False) -> bytes:
    output = io.BytesIO()
    image = Image.new("RGB", (3, 2), (30, 60, 90))
    kwargs = {}
    if exif:
        metadata = Image.Exif()
        metadata[0x010E] = "private metadata"
        kwargs["exif"] = metadata
    image.save(output, format=format_name, **kwargs)
    return output.getvalue()


def make_service(tmp_path: Path) -> tuple[AssetService, Path, StoredMarkdown, LocalFileStorage]:
    database = initialize_database(tmp_path / "assets.db")
    reader = StoredMarkdown()
    storage = LocalFileStorage(tmp_path / "blobs")
    return (
        AssetService(database, storage=storage, markdown_source_reader=reader, clock=lambda: NOW),
        database,
        reader,
        storage,
    )


def test_upload_normalizes_strips_metadata_and_deduplicates(tmp_path: Path) -> None:
    service, database, _, storage = make_service(tmp_path)

    first = service.upload_image(
        encoded_image("JPEG", exif=True),
        original_name="../../private\\camera.jpg",
        upload_session_id="session-1",
    )
    second = service.upload_image(
        encoded_image("JPEG", exif=False),
        original_name="other.jpg",
        upload_session_id="session-2",
    )

    assert second["id"] == first["id"]
    assert first["media_type"] == "image/jpeg"
    assert first["original_name"] == "camera.jpg"
    assert storage.exists(first["storage_key"])
    with Image.open(io.BytesIO(storage.open(first["storage_key"]))) as normalized:
        assert normalized.getexif() == {}
    with database_connection(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM asset_references").fetchone()[0] == 2


@pytest.mark.parametrize(
    "content,name",
    [
        (b"<svg onload='alert(1)'></svg>", "attack.svg"),
        (encoded_image("GIF"), "animated-or-gif.gif"),
        (b"not an image", "fake.png"),
        (b"", "empty.png"),
    ],
)
def test_upload_rejects_unsupported_or_malformed_content(
    tmp_path: Path, content: bytes, name: str
) -> None:
    service, _, _, _ = make_service(tmp_path)
    with pytest.raises(InvalidImageError):
        service.upload_image(content, original_name=name, upload_session_id="session-1")


def test_upload_accepts_png_and_static_webp(tmp_path: Path) -> None:
    service, _, _, _ = make_service(tmp_path)
    png = service.upload_image(encoded_image("PNG"), original_name="a.png", upload_session_id="p")
    webp = service.upload_image(encoded_image("WEBP"), original_name="a.webp", upload_session_id="w")
    assert (png["media_type"], webp["media_type"]) == ("image/png", "image/webp")


def test_reconcile_reads_server_markdown_and_atomically_replaces_owner_refs(tmp_path: Path) -> None:
    service, database, reader, _ = make_service(tmp_path)
    first = service.upload_image(encoded_image("PNG"), original_name="1.png", upload_session_id="s1")
    second_image = Image.new("RGB", (2, 2), (1, 2, 3))
    encoded = io.BytesIO()
    second_image.save(encoded, format="PNG")
    second = service.upload_image(encoded.getvalue(), original_name="2.png", upload_session_id="s2")
    reader.values[("note", "13", "content")] = f"![first](image://{first['id']})"

    assert service.reconcile(owner_type="note", owner_id="13", field="content") == (first["id"],)
    reader.values[("note", "13", "content")] = f"![second](image://{second['id']})"
    assert service.reconcile(owner_type="note", owner_id="13", field="content") == (second["id"],)
    assert reader.calls == [("note", "13", "content"), ("note", "13", "content")]

    with database_connection(database) as connection:
        refs = list_references(connection, owner_type="note", owner_id="13", field="content")
        assert [reference["asset_id"] for reference in refs] == [second["id"]]
        assert connection.execute(
            "SELECT COUNT(*) FROM asset_references WHERE owner_type='upload_session'"
        ).fetchone()[0] == 2


def test_reconcile_missing_asset_rolls_back_existing_references(tmp_path: Path) -> None:
    service, database, reader, _ = make_service(tmp_path)
    asset = service.upload_image(encoded_image("PNG"), original_name="1.png", upload_session_id="s1")
    reader.values[("publication_version", "7", "markdown")] = f"![ok](image://{asset['id']})"
    service.reconcile(owner_type="publication_version", owner_id="7", field="markdown")

    reader.values[("publication_version", "7", "markdown")] = "![missing](image://999999)"
    with pytest.raises(AssetNotFoundError):
        service.reconcile(owner_type="publication_version", owner_id="7", field="markdown")

    with database_connection(database) as connection:
        refs = list_references(
            connection, owner_type="publication_version", owner_id="7", field="markdown"
        )
        assert [reference["asset_id"] for reference in refs] == [asset["id"]]


def test_reconcile_requires_server_source_and_has_no_client_asset_list(tmp_path: Path) -> None:
    service, _, _, _ = make_service(tmp_path)
    with pytest.raises(MarkdownSourceNotFoundError):
        service.reconcile(owner_type="note", owner_id="13", field="content")
    with pytest.raises(TypeError):
        service.reconcile(  # type: ignore[call-arg]
            owner_type="note", owner_id="13", field="content", asset_ids=[1]
        )
