from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SourceReference:
    source_app: str
    source_type: str
    source_id: str


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    reference: SourceReference
    title: str


class SourceReferenceProvider(Protocol):
    def resolve(self, reference: SourceReference) -> ResolvedSource | None: ...


class SourceReferenceResolver:
    def __init__(
        self,
        providers: dict[tuple[str, str], SourceReferenceProvider] | None = None,
    ) -> None:
        self._providers = dict(providers or {})

    def supports(self, source_app: str, source_type: str) -> bool:
        return (source_app, source_type) in self._providers

    def resolve(self, reference: SourceReference) -> ResolvedSource | None:
        provider = self._providers.get(
            (reference.source_app, reference.source_type)
        )
        if provider is None:
            raise ValueError(
                f"unsupported source: {reference.source_app}/{reference.source_type}"
            )
        return provider.resolve(reference)
