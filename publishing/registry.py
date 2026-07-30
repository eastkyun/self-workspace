from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

from publishing.contracts import PublicationType, RenderOptions, SlugPolicy

_TYPE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]*$")
_ROUTE_PATTERN = re.compile(r"^/[a-z][a-z0-9-]*$")
_LOCAL_ICON_PREFIX = "/static/icons/"
_REQUIRED_HOOKS = (
    "render_options",
    "validate_publish",
    "build_view_context",
    "navigation_provider",
    "search_provider",
    "related_content_provider",
)


@dataclass(frozen=True, slots=True, order=True)
class RegistryIssue:
    code: str
    provider: str
    field: str
    message: str


class PublicationRegistryValidationError(ValueError):
    def __init__(self, issues: list[RegistryIssue] | tuple[RegistryIssue, ...]):
        self.issues = tuple(sorted(issues))
        details = "\n".join(
            f"- [{issue.code}] provider={issue.provider} "
            f"field={issue.field}: {issue.message}"
            for issue in self.issues
        )
        super().__init__(f"publication type registry validation failed:\n{details}")


def _safe_local_file(root: Path, relative: str) -> bool:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return False
    return candidate.is_file()


def _validate_provider(
    provider: Any,
    *,
    icon_root: Path,
    template_root: Path,
) -> list[RegistryIssue]:
    issues: list[RegistryIssue] = []
    provider_name = type(provider).__name__

    def issue(code: str, field: str, message: str) -> None:
        issues.append(RegistryIssue(code, provider_name, field, message))

    type_id = getattr(provider, "type_id", None)
    if not isinstance(type_id, str) or not _TYPE_ID_PATTERN.fullmatch(type_id):
        issue("invalid_type_id", "type_id", "must be a lowercase canonical ID")

    display_name = getattr(provider, "display_name", None)
    if not isinstance(display_name, str) or not display_name.strip():
        issue("invalid_display_name", "display_name", "must be a non-empty string")

    order = getattr(provider, "order", None)
    if type(order) is not int or order < 0:
        issue("invalid_order", "order", "must be a non-negative integer")

    if type(getattr(provider, "enabled", None)) is not bool:
        issue("invalid_enabled", "enabled", "must be a boolean")

    route = getattr(provider, "route_prefix", None)
    if not isinstance(route, str) or not _ROUTE_PATTERN.fullmatch(route):
        issue(
            "invalid_route_prefix",
            "route_prefix",
            "must be one canonical top-level local path segment",
        )
    elif urlsplit(route).path != route:
        issue("invalid_route_prefix", "route_prefix", "query and fragment are forbidden")

    icon = getattr(provider, "icon", None)
    if not isinstance(icon, str) or not icon.startswith(_LOCAL_ICON_PREFIX):
        issue("unsafe_icon", "icon", "must be a local /static/icons/ asset")
    else:
        icon_path = PurePosixPath(icon)
        relative_icon = str(icon_path.relative_to("/static"))
        if ".." in icon_path.parts or not _safe_local_file(icon_root, relative_icon):
            issue("missing_or_unsafe_icon", "icon", "local icon does not exist safely")

    template = getattr(provider, "template_name", None)
    if not isinstance(template, str) or not template or Path(template).is_absolute():
        issue("unsafe_template", "template_name", "must be a relative template path")
    elif ".." in PurePosixPath(template).parts or not _safe_local_file(
        template_root, template
    ):
        issue(
            "missing_or_unsafe_template",
            "template_name",
            "template does not exist below the publishing template root",
        )

    slug_policy = getattr(provider, "slug_policy", None)
    if not isinstance(slug_policy, SlugPolicy):
        issue("invalid_slug_policy", "slug_policy", "must be a SlugPolicy")
    else:
        try:
            re.compile(slug_policy.pattern)
        except re.error:
            issue("invalid_slug_pattern", "slug_policy", "pattern is not valid regex")
        if slug_policy.min_length < 1 or slug_policy.max_length < slug_policy.min_length:
            issue("invalid_slug_length", "slug_policy", "length bounds are invalid")
        if len(slug_policy.reserved_words) != len(set(slug_policy.reserved_words)):
            issue("duplicate_reserved_slug", "slug_policy", "reserved words repeat")

    for hook in _REQUIRED_HOOKS:
        candidate = getattr(provider, hook, None)
        if not callable(candidate):
            issue("missing_hook", hook, "required provider hook must be callable")

    render_options = getattr(provider, "render_options", None)
    if callable(render_options):
        try:
            options = render_options()
        except Exception as error:  # validation reports all provider defects together
            issue("hook_error", "render_options", f"raised {type(error).__name__}")
        else:
            if not isinstance(options, RenderOptions):
                issue("invalid_render_options", "render_options", "must return RenderOptions")
            elif any(level not in range(1, 7) for level in options.toc_levels):
                issue("invalid_toc_level", "render_options", "TOC levels must be H1-H6")

    for hook, minimum_parameters in (
        ("validate_publish", 1),
        ("build_view_context", 3),
    ):
        candidate = getattr(provider, hook, None)
        if callable(candidate):
            try:
                signature = inspect.signature(candidate)
            except (TypeError, ValueError):
                issue("invalid_hook_signature", hook, "signature cannot be inspected")
            else:
                positional = [
                    parameter
                    for parameter in signature.parameters.values()
                    if parameter.kind
                    in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
                ]
                if len(positional) < minimum_parameters:
                    issue(
                        "invalid_hook_signature",
                        hook,
                        f"must accept at least {minimum_parameters} positional arguments",
                    )
    return issues


class PublicationTypeRegistry:
    """Immutable, validated snapshot of explicitly supplied type providers."""

    def __init__(
        self,
        providers: tuple[PublicationType, ...] | list[PublicationType],
        *,
        icon_root: Path,
        template_root: Path,
    ) -> None:
        provider_snapshot = tuple(providers)
        issues: list[RegistryIssue] = []
        for provider in provider_snapshot:
            issues.extend(
                _validate_provider(
                    provider,
                    icon_root=icon_root,
                    template_root=template_root,
                )
            )

        by_id: dict[str, PublicationType] = {}
        by_route: dict[str, PublicationType] = {}
        by_order: dict[int, PublicationType] = {}
        for provider in provider_snapshot:
            provider_name = type(provider).__name__
            type_id = getattr(provider, "type_id", None)
            route = getattr(provider, "route_prefix", None)
            order = getattr(provider, "order", None)
            if isinstance(type_id, str):
                if type_id in by_id:
                    issues.append(
                        RegistryIssue("duplicate_type_id", provider_name, "type_id", type_id)
                    )
                else:
                    by_id[type_id] = provider
            if isinstance(route, str):
                if route in by_route:
                    issues.append(
                        RegistryIssue("duplicate_route", provider_name, "route_prefix", route)
                    )
                else:
                    by_route[route] = provider
            if type(order) is int:
                if order in by_order:
                    issues.append(
                        RegistryIssue("duplicate_order", provider_name, "order", str(order))
                    )
                else:
                    by_order[order] = provider

        if issues:
            raise PublicationRegistryValidationError(issues)

        self._providers = tuple(
            sorted(provider_snapshot, key=lambda item: (item.order, item.type_id))
        )
        self._by_id = {provider.type_id: provider for provider in self._providers}

    def get_all(self) -> tuple[PublicationType, ...]:
        return self._providers

    def get_enabled(self) -> tuple[PublicationType, ...]:
        return tuple(provider for provider in self._providers if provider.enabled)

    def get_creation_types(self) -> tuple[PublicationType, ...]:
        return self.get_enabled()

    def get(self, type_id: str) -> PublicationType | None:
        return self._by_id.get(type_id)

    def require(self, type_id: str) -> PublicationType:
        provider = self.get(type_id)
        if provider is None:
            raise KeyError(type_id)
        return provider

    def exists(self, type_id: str) -> bool:
        return type_id in self._by_id

    def resolve_route(self, path: str) -> PublicationType | None:
        parsed = urlsplit(path)
        if parsed.scheme or parsed.netloc:
            return None
        candidate = parsed.path
        for provider in self._providers:
            prefix = provider.route_prefix
            if candidate == prefix or candidate.startswith(f"{prefix}/"):
                return provider
        return None

    def validate_stored_type_ids(self, type_ids: tuple[str, ...] | list[str]) -> None:
        unknown = sorted(set(type_ids) - self._by_id.keys())
        if unknown:
            raise PublicationRegistryValidationError(
                [
                    RegistryIssue(
                        "unknown_stored_type",
                        "stored-publication",
                        "type_id",
                        type_id,
                    )
                    for type_id in unknown
                ]
            )


def create_default_registry(
    *,
    icon_root: Path,
    template_root: Path,
    additional_providers: tuple[PublicationType, ...] = (),
) -> PublicationTypeRegistry:
    """Compose built-ins explicitly; no discovery or import registration occurs."""

    from publishing.types import default_publication_types

    return PublicationTypeRegistry(
        (*default_publication_types(), *additional_providers),
        icon_root=icon_root,
        template_root=template_root,
    )


__all__ = [
    "PublicationRegistryValidationError",
    "PublicationTypeRegistry",
    "RegistryIssue",
    "create_default_registry",
]
