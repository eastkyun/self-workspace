from __future__ import annotations

import io
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Protocol

from PIL import Image, UnidentifiedImageError

from . import repository
from .database import database_connection
from .markdown import extract_asset_ids
from .storage import LocalFileStorage, StorageProvider, content_storage_key, digest_bytes

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MAX_DIMENSION = 10_000
_OWNER_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_FIELD = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class AssetError(RuntimeError):
    pass


class InvalidImageError(AssetError):
    pass


class AssetNotFoundError(AssetError):
    pass


class MarkdownSourceNotFoundError(AssetError):
    pass


class MarkdownSourceReader(Protocol):
    def get_markdown(self, owner_type: str, owner_id: str, field: str) -> str | None: ...


def _safe_original_name(name: str, extension: str) -> str:
    leaf = name.replace("\\", "/").rsplit("/", 1)[-1]
    leaf = "".join(character for character in leaf if character >= " " and character != "\x7f").strip()
    if not leaf:
        return f"upload.{extension}"
    return leaf[:255]


def _normalize_image(content: bytes) -> tuple[bytes, str, str]:
    if not content or len(content) > MAX_UPLOAD_BYTES:
        raise InvalidImageError("image upload size is outside the allowed range")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise InvalidImageError("only PNG, JPEG, and static WebP are supported")
            if getattr(image, "is_animated", False) or getattr(image, "n_frames", 1) != 1:
                raise InvalidImageError("animated images are not supported")
            width, height = image.size
            if width <= 0 or height <= 0 or width > MAX_DIMENSION or height > MAX_DIMENSION:
                raise InvalidImageError("image dimensions are outside the allowed range")
            if width * height > MAX_IMAGE_PIXELS:
                raise InvalidImageError("image pixel count exceeds the allowed limit")
            image.load()
            output = io.BytesIO()
            if image.format == "PNG":
                normalized = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")
                normalized.save(output, format="PNG", optimize=False, compress_level=9)
                return output.getvalue(), "image/png", "png"
            if image.format == "JPEG":
                image.convert("RGB").save(
                    output, format="JPEG", quality=90, optimize=False, progressive=False, subsampling=0
                )
                return output.getvalue(), "image/jpeg", "jpg"
            normalized = image.convert("RGBA") if "A" in image.getbands() else image.convert("RGB")
            normalized.save(output, format="WEBP", lossless=True, method=6, exact=True)
            return output.getvalue(), "image/webp", "webp"
    except InvalidImageError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise InvalidImageError("uploaded data is not a safe supported image") from error


class AssetService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        storage: StorageProvider,
        markdown_source_reader: MarkdownSourceReader,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.storage = storage
        self.markdown_source_reader = markdown_source_reader
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> str:
        return self.clock().astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _validate_owner(owner_type: str, owner_id: str, field: str) -> None:
        if not _FIELD.fullmatch(owner_type) or not _OWNER_PART.fullmatch(owner_id) or not _FIELD.fullmatch(field):
            raise ValueError("invalid asset reference owner")

    def upload_image(self, content: bytes, *, original_name: str, upload_session_id: str) -> dict:
        self._validate_owner("upload_session", upload_session_id, "upload")
        normalized, media_type, extension = _normalize_image(content)
        sha256 = digest_bytes(normalized)
        storage_key = content_storage_key(sha256, extension)
        self.storage.put(storage_key, normalized)
        now = self._now()
        with database_connection(self.database_path) as connection:
            asset = repository.get_asset_by_sha256(connection, sha256)
            if asset is None:
                asset = repository.create_asset(
                    connection,
                    sha256=sha256,
                    media_type=media_type,
                    byte_size=len(normalized),
                    storage_key=storage_key,
                    original_name=_safe_original_name(original_name, extension),
                    created_at=now,
                )
            repository.add_reference(
                connection,
                asset_id=asset["id"],
                owner_type="upload_session",
                owner_id=upload_session_id,
                field="upload",
                created_at=now,
            )
            return asset

    def reconcile(self, *, owner_type: str, owner_id: str, field: str) -> tuple[int, ...]:
        self._validate_owner(owner_type, owner_id, field)
        markdown = self.markdown_source_reader.get_markdown(owner_type, owner_id, field)
        if markdown is None:
            raise MarkdownSourceNotFoundError("stored Markdown source was not found")
        asset_ids = extract_asset_ids(markdown)
        now = self._now()
        with database_connection(self.database_path) as connection:
            missing = [asset_id for asset_id in asset_ids if repository.get_asset(connection, asset_id) is None]
            if missing:
                raise AssetNotFoundError(f"stored Markdown references missing assets: {missing}")
            repository.replace_owner_references(
                connection,
                owner_type=owner_type,
                owner_id=owner_id,
                field=field,
                asset_ids=asset_ids,
                created_at=now,
            )
        return asset_ids

    def get_asset(self, asset_id: int) -> dict:
        if asset_id <= 0:
            raise AssetNotFoundError("asset was not found")
        with database_connection(self.database_path) as connection:
            asset = repository.get_asset(connection, asset_id)
        if asset is None:
            raise AssetNotFoundError("asset was not found")
        return asset
