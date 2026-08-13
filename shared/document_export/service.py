from __future__ import annotations

import re
import json
import tempfile
import threading
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from .contracts import DocumentSnapshot, ExportedDocument, ExportFormat
from .errors import ExportUnavailable
from .markdown import AssetLoader, normalize_markdown
from .runner import PandocRunner

_MEDIA_TYPES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
_UNSAFE_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_KST = timezone(timedelta(hours=9))


def export_date(value: str) -> str:
    """Normalize timestamp metadata to the workspace's UTC+09:00 timezone."""
    raw = str(value or "").strip()
    if not raw or "T" not in raw:
        return raw
    try:
        parsed = datetime.fromisoformat(raw.removesuffix("Z") + ("+00:00" if raw.endswith("Z") else ""))
    except ValueError:
        return raw
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(_KST).isoformat(timespec="seconds")


def safe_filename(title: str, format: ExportFormat) -> str:
    stem = _UNSAFE_FILENAME.sub("-", str(title or "")).strip(" .")
    stem = re.sub(r"\s+", " ", stem)[:80].rstrip(" .") or "document"
    return f"{stem}.{format}"


def attachment_headers(filename: str, *, public: bool = False) -> dict[str, str]:
    ascii_name = _UNSAFE_FILENAME.sub("-", filename).encode("ascii", "ignore").decode()
    ascii_name = ascii_name or "document"
    encoded = quote(filename, safe="")
    return {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded}'
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "no-store" if not public else "public, max-age=0, must-revalidate",
    }


class DocumentExportService:
    def __init__(
        self,
        runner: PandocRunner | object | None = None,
        *,
        max_concurrent: int = 2,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        self.runner = runner or PandocRunner()
        self._slots = threading.BoundedSemaphore(max_concurrent)

    def export(
        self,
        snapshot: DocumentSnapshot,
        format: ExportFormat,
        *,
        asset_loader: AssetLoader | None = None,
    ) -> ExportedDocument:
        if format not in _MEDIA_TYPES:
            raise ValueError("unsupported document export format")
        if not self._slots.acquire(blocking=False):
            raise ExportUnavailable("document export capacity is busy")
        try:
            with tempfile.TemporaryDirectory(prefix="workspace-export-") as temporary:
                root = Path(temporary)
                body = normalize_markdown(
                    snapshot.markdown,
                    resource_directory=root,
                    asset_loader=asset_loader,
                )
                front_matter = ["---", f"title: {json.dumps(snapshot.title, ensure_ascii=False)}"]
                if snapshot.author:
                    front_matter.append(
                        f"author: {json.dumps(snapshot.author, ensure_ascii=False)}"
                    )
                if snapshot.date:
                    front_matter.append(
                        f"date: {json.dumps(export_date(snapshot.date), ensure_ascii=False)}"
                    )
                front_matter.extend(["---", ""])
                if snapshot.summary:
                    front_matter.extend([f"> {snapshot.summary}", ""])
                source = root / "document.md"
                source.write_text("\n".join(front_matter) + body, encoding="utf-8")
                output = root / f"document.{format}"
                self.runner.convert(
                    source,
                    output,
                    format=format,
                    resource_path=root,
                )
                content = output.read_bytes()
                if not content:
                    raise ExportUnavailable("document converter produced an empty file")
                return ExportedDocument(
                    content=content,
                    media_type=_MEDIA_TYPES[format],
                    filename=safe_filename(snapshot.title, format),
                )
        finally:
            self._slots.release()
