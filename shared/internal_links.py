from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.app_registry import AppRegistry

MAX_INTERNAL_LINKS = 50


class InternalLinkReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    target: str = Field(min_length=1, max_length=120)

    @field_validator("target")
    @classmethod
    def validate_target(cls, target: str) -> str:
        if not target.isdecimal() or int(target) <= 0:
            raise ValueError("internal link target must be a positive decimal")
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


def _safe_workspace_href(href: str, app_registry: AppRegistry) -> bool:
    parsed = urlsplit(href)
    if (
        parsed.scheme
        or parsed.netloc
        or parsed.fragment
        or not parsed.path.startswith("/apps/")
        or "\\" in href
        or "//" in parsed.path
    ):
        return False
    decoded_path = unquote(parsed.path)
    if unquote(decoded_path) != decoded_path:
        return False
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        return False
    app = app_registry.resolve_route(decoded_path)
    return bool(app and app.kind == "internal" and decoded_path.startswith(app.route))


class InternalLinkResolverRegistry:
    def __init__(
        self,
        providers: Mapping[str, InternalLinkProvider],
        app_registry: AppRegistry,
    ) -> None:
        normalized = dict(providers)
        if any(type_id != provider.type_id for type_id, provider in normalized.items()):
            raise ValueError("internal link provider key must match type_id")
        self._providers = normalized
        self._app_registry = app_registry

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
                    and _safe_workspace_href(link.href, self._app_registry)
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

