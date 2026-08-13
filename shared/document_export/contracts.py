from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ExportFormat = Literal["docx", "pdf"]


@dataclass(frozen=True, slots=True)
class AssetResource:
    content: bytes
    media_type: str


@dataclass(frozen=True, slots=True)
class DocumentSnapshot:
    title: str
    markdown: str
    summary: str = ""
    author: str = ""
    version: str = ""
    date: str = ""


@dataclass(frozen=True, slots=True)
class ExportedDocument:
    content: bytes
    media_type: str
    filename: str
