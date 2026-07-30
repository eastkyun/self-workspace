from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class PublicPublicationContent:
    publication: Mapping[str, Any]
    version: Mapping[str, Any]

    @classmethod
    def from_rows(
        cls,
        publication: dict[str, Any],
        version: dict[str, Any],
    ) -> "PublicPublicationContent":
        return cls(
            publication=MappingProxyType(dict(publication)),
            version=MappingProxyType(dict(version)),
        )


__all__ = ("PublicPublicationContent",)
