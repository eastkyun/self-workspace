from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_INTERNAL_LINKS = 50
_CANONICAL_TARGET_PATTERN = r"(?:[1-9][0-9]*|[a-z0-9]+(?:-[a-z0-9]+)*)"


class InternalLinkReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    target: str = Field(
        min_length=1,
        max_length=120,
        pattern=rf"^{_CANONICAL_TARGET_PATTERN}$",
    )

    @field_validator("target")
    @classmethod
    def validate_target(cls, target: str) -> str:
        return target


class InternalLinkBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    links: tuple[InternalLinkReference, ...] = Field(
        min_length=1,
        max_length=MAX_INTERNAL_LINKS,
    )


class InternalLinkResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str
    target: str
    exists: bool
    href: str | None = None
    label: str


class ProviderLink(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: str
    href: str
    label: str


class InternalLinkProvider(Protocol):
    type_id: str

    def resolve_many(self, targets: tuple[str, ...]) -> Mapping[str, ProviderLink]:
        """Resolve targets without writing to application storage."""


class InternalRouteRegistry(Protocol):
    def resolve_route(self, path: str) -> object | None:
        """Return route metadata when the local path belongs to this registry."""


def _safe_workspace_href(
    href: str,
    route_registries: Sequence[InternalRouteRegistry],
) -> bool:
    parsed = urlsplit(href)
    if (
        not href.startswith("/")
        or any(ord(character) < 32 or ord(character) == 127 for character in href)
        or parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or not parsed.path.startswith("/")
        or "\\" in href
        or "//" in parsed.path
    ):
        return False
    decoded_path = unquote(parsed.path)
    if decoded_path != parsed.path or unquote(decoded_path) != decoded_path:
        return False
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        return False
    for registry in route_registries:
        route = registry.resolve_route(decoded_path)
        if route is None:
            continue
        # AppRegistry external entries are never valid internal-link targets.
        if getattr(route, "kind", "internal") != "internal":
            continue
        return True
    return False


class InternalLinkResolverRegistry:
    def __init__(
        self,
        providers: Mapping[str, InternalLinkProvider],
        app_registry: InternalRouteRegistry,
        *,
        additional_route_registries: Sequence[InternalRouteRegistry] = (),
    ) -> None:
        normalized = dict(providers)
        if any(type_id != provider.type_id for type_id, provider in normalized.items()):
            raise ValueError("internal link provider key must match type_id")
        self._providers = normalized
        self._route_registries = (app_registry, *tuple(additional_route_registries))

    def resolve_batch(
        self,
        references: tuple[InternalLinkReference, ...],
    ) -> tuple[InternalLinkResult, ...]:
        if len(references) > MAX_INTERNAL_LINKS:
            raise ValueError(f"at most {MAX_INTERNAL_LINKS} internal links are allowed")

        unique = tuple(
            dict.fromkeys((reference.type, reference.target) for reference in references)
        )
        grouped: dict[str, list[str]] = {}
        for type_id, target in unique:
            grouped.setdefault(type_id, []).append(target)

        provider_results: dict[tuple[str, str], ProviderLink] = {}
        failed_types: set[str] = set()
        for type_id, targets in grouped.items():
            provider = self._providers.get(type_id)
            if provider is None:
                continue
            try:
                resolved = provider.resolve_many(tuple(targets))
            except Exception:
                failed_types.add(type_id)
                continue
            for target, link in resolved.items():
                if (
                    target in targets
                    and link.target == target
                    and _safe_workspace_href(link.href, self._route_registries)
                ):
                    provider_results[(type_id, target)] = link

        results: list[InternalLinkResult] = []
        for type_id, target in unique:
            link = provider_results.get((type_id, target))
            if link is None:
                fallback = (
                    "확인할 수 없는 링크"
                    if type_id in failed_types
                    else f"{type_id}:{target}"
                )
                results.append(
                    InternalLinkResult(
                        type=type_id,
                        target=target,
                        exists=False,
                        label=fallback,
                    )
                )
                continue
            results.append(
                InternalLinkResult(
                    type=type_id,
                    target=target,
                    exists=True,
                    href=link.href,
                    label=link.label,
                )
            )
        return tuple(results)
