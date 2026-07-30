from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from publishing.contracts import (
    PublicationSnapshot,
    PublicationView,
    RenderOptions,
    SlugPolicy,
    VersionView,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TYPE_MANIFEST_PATH = (
    WORKSPACE_ROOT / "instance" / "publishing" / "types.json"
)
_TYPE_ID = re.compile(r"^[a-z][a-z0-9-]*$")
DECLARATIVE_ROUTE_BASE = "/publications/view"


def declarative_route_prefix(type_id: str) -> str:
    return f"{DECLARATIVE_ROUTE_BASE}/{type_id}"


class PublicationTypeDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type_id: str = Field(min_length=1, max_length=40)
    display_name: str = Field(min_length=1, max_length=80)
    order: int = Field(ge=0, le=10_000)
    enabled: bool = True
    toc_enabled: bool = False
    related_enabled: bool = False
    version_visible: bool = True

    @field_validator("type_id")
    @classmethod
    def validate_type_id(cls, value: str) -> str:
        if not _TYPE_ID.fullmatch(value):
            raise ValueError("type_id must be canonical lowercase ASCII")
        return value

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("display_name must not be blank")
        return normalized


@dataclass(frozen=True, slots=True)
class DeclarativePublicationType:
    type_id: str
    display_name: str
    order: int
    enabled: bool = True
    toc_enabled: bool = False
    related_enabled: bool = False
    version_visible: bool = True
    icon: str = "/static/icons/publication.svg"
    template_name: str = "publication_viewer.html"
    slug_policy: SlugPolicy = field(default_factory=SlugPolicy)

    @property
    def route_prefix(self) -> str:
        return declarative_route_prefix(self.type_id)

    def render_options(self) -> RenderOptions:
        return RenderOptions(
            toc_enabled=self.toc_enabled,
            toc_levels=(2, 3, 4),
            related_enabled=self.related_enabled,
            version_visible=self.version_visible,
        )

    def validate_publish(self, snapshot: PublicationSnapshot) -> tuple[str, ...]:
        return ()

    def build_view_context(
        self,
        publication: PublicationView,
        version: VersionView,
        context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return MappingProxyType({})

    def navigation_provider(self) -> object | None:
        return None

    def search_provider(self) -> object | None:
        return None

    def related_content_provider(self) -> object | None:
        return None


class PublicationTypeManifestStore:
    """Explicit declarative provider config; never scans application folders."""

    def __init__(self, path: str | Path | None = None) -> None:
        configured = path or os.getenv("PUBLICATION_TYPES_PATH")
        self.path = Path(
            configured or DEFAULT_TYPE_MANIFEST_PATH
        ).expanduser().resolve()

    def get_all(self) -> tuple[PublicationTypeDefinition, ...]:
        if not self.path.is_file():
            return ()
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if document.get("schema_version") != 1:
            raise RuntimeError("unsupported publication type manifest schema")
        return tuple(
            sorted(
                (
                    PublicationTypeDefinition.model_validate(item)
                    for item in document.get("types", [])
                ),
                key=lambda item: (item.order, item.type_id),
            )
        )

    def providers(self) -> tuple[DeclarativePublicationType, ...]:
        return tuple(
            DeclarativePublicationType(**definition.model_dump())
            for definition in self.get_all()
        )

    def get(self, type_id: str) -> PublicationTypeDefinition | None:
        return next(
            (item for item in self.get_all() if item.type_id == type_id),
            None,
        )

    def save(
        self,
        definition: PublicationTypeDefinition,
        *,
        replace: bool,
    ) -> PublicationTypeDefinition:
        definitions = {item.type_id: item for item in self.get_all()}
        exists = definition.type_id in definitions
        if exists and not replace:
            raise FileExistsError(definition.type_id)
        if not exists and replace:
            raise FileNotFoundError(definition.type_id)
        definitions[definition.type_id] = definition
        self._write(tuple(definitions.values()))
        return definition

    def delete(self, type_id: str) -> None:
        definitions = {item.type_id: item for item in self.get_all()}
        if type_id not in definitions:
            raise FileNotFoundError(type_id)
        del definitions[type_id]
        self._write(tuple(definitions.values()))

    def _write(self, definitions: tuple[PublicationTypeDefinition, ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "types": [
                item.model_dump()
                for item in sorted(
                    definitions,
                    key=lambda value: (value.order, value.type_id),
                )
            ],
        }
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".publication-types-",
            suffix=".json",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
                json.dump(payload, temporary, ensure_ascii=False, indent=2)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


__all__ = (
    "DEFAULT_TYPE_MANIFEST_PATH",
    "DECLARATIVE_ROUTE_BASE",
    "DeclarativePublicationType",
    "PublicationTypeDefinition",
    "PublicationTypeManifestStore",
    "declarative_route_prefix",
)
