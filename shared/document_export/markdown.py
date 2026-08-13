from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Callable

from PIL import Image, UnidentifiedImageError

from .contracts import AssetResource
from .errors import ExportResourceLimitExceeded

MAX_SOURCE_LENGTH = 200_000
MAX_ASSET_IMAGES = 50
MAX_ASSET_BYTES = 50 * 1024 * 1024

_IMAGE = re.compile(
    r"!\[([^\]\n]*)\]\(image://([1-9][0-9]*)(?:\s+\"([^\"\n]*)\")?\)"
)
_INTERNAL_LINK = re.compile(
    r"\[\[([a-z][a-z0-9-]*):([1-9][0-9]*|[a-z0-9]+(?:-[a-z0-9]+)*)(?:\|([^\]\n]+))?\]\]"
)
_MARKDOWN_IMAGE = re.compile(
    r"!\[([^\]\n]*)\]\(([^)\n]+)\)"
)
_REFERENCE_IMAGE = re.compile(r"!\[([^\]\n]*)\]\[[^\]\n]*\]")
_EXTENSIONS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "png"}

AssetLoader = Callable[[int], AssetResource | None]


def _safe_label(value: str) -> str:
    return value.replace("[", "\\[").replace("]", "\\]")


def _stage_asset(resource: AssetResource, path: Path) -> None:
    if resource.media_type != "image/webp":
        path.write_bytes(resource.content)
        return
    try:
        with Image.open(io.BytesIO(resource.content)) as image:
            image.load()
            image.save(path, format="PNG")
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ExportConversionFailed("export image could not be decoded") from error


def normalize_markdown(
    markdown: str,
    *,
    resource_directory: Path,
    asset_loader: AssetLoader | None = None,
) -> str:
    source = str(markdown or "")
    if len(source) > MAX_SOURCE_LENGTH:
        raise ExportResourceLimitExceeded("Markdown exceeds the export size limit")

    seen: dict[int, str | None] = {}
    total_bytes = 0

    def replace_image(match: re.Match[str]) -> str:
        nonlocal total_bytes
        alt, raw_id, title = match.groups()
        asset_id = int(raw_id)
        if asset_id not in seen:
            if len(seen) >= MAX_ASSET_IMAGES:
                raise ExportResourceLimitExceeded("Too many images for document export")
            resource = asset_loader(asset_id) if asset_loader is not None else None
            if resource is None or resource.media_type not in _EXTENSIONS:
                seen[asset_id] = None
            else:
                total_bytes += len(resource.content)
                if total_bytes > MAX_ASSET_BYTES:
                    raise ExportResourceLimitExceeded(
                        "Images exceed the document export size limit"
                    )
                relative = f"assets/{asset_id}.{_EXTENSIONS[resource.media_type]}"
                target = resource_directory / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                _stage_asset(resource, target)
                seen[asset_id] = relative
        relative = seen[asset_id]
        if relative is None:
            return _safe_label(alt or f"이미지 {asset_id}") + " [이미지를 사용할 수 없음]"
        suffix = f' "{title}"' if title else ""
        return f"![{_safe_label(alt)}]({relative}{suffix})"

    normalized = _IMAGE.sub(replace_image, source)

    def reject_unstaged_image(match: re.Match[str]) -> str:
        alt, target = match.groups()
        if re.fullmatch(r"assets/[1-9][0-9]*\.(?:png|jpg)", target):
            return match.group(0)
        return _safe_label(alt or "이미지") + " [이미지를 사용할 수 없음]"

    normalized = _MARKDOWN_IMAGE.sub(reject_unstaged_image, normalized)
    normalized = _REFERENCE_IMAGE.sub(
        lambda match: _safe_label(match.group(1) or "이미지")
        + " [이미지를 사용할 수 없음]",
        normalized,
    )

    def replace_internal_link(match: re.Match[str]) -> str:
        type_id, target, label = match.groups()
        return _safe_label((label or f"{type_id}:{target}").strip())

    return _INTERNAL_LINK.sub(replace_internal_link, normalized)
