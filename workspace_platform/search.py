"""Permission-aware, workspace-scoped search provider aggregation.

Providers own their domain queries.  This module only applies the shared app
and permission policy, invokes each provider with the immutable request
context, and produces a deterministic projection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from access.context import PermissionSnapshot, WorkspaceContext
from access.errors import AuthorizationError


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A provider-owned result that is safe to render in workspace search."""

    workspace_id: str
    resource_id: str
    title: str
    url: str
    score: float = 0.0
    summary: str = ""
    updated_at: str | None = None


@runtime_checkable
class WorkspaceSearchProvider(Protocol):
    """Contract implemented by searchable workspace applications."""

    @property
    def key(self) -> str: ...

    @property
    def app_key(self) -> str: ...

    @property
    def required_permissions(self) -> tuple[str, ...]: ...

    def search(
        self,
        context: WorkspaceContext,
        query: str,
        limit: int,
        offset: int,
    ) -> tuple[SearchResult, ...]: ...


class SearchAuthorizationPolicy(Protocol):
    """The subset of AuthorizationService used by search aggregation."""

    def permission_snapshot(self, context: WorkspaceContext) -> PermissionSnapshot: ...

    def authorize_app(
        self,
        context: WorkspaceContext,
        app_key: str,
        *,
        snapshot: PermissionSnapshot | None = None,
    ) -> bool: ...

    def authorize(
        self,
        context: WorkspaceContext,
        permission_code: str,
        resource_owner_id: str | None = None,
        *,
        snapshot: PermissionSnapshot | None = None,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class SearchGroup:
    provider_key: str
    app_key: str
    results: tuple[SearchResult, ...]


@dataclass(frozen=True, slots=True)
class SearchProviderFailure:
    provider_key: str
    app_key: str
    code: str = "provider_unavailable"


@dataclass(frozen=True, slots=True)
class WorkspaceSearchResponse:
    query: str
    groups: tuple[SearchGroup, ...]
    failures: tuple[SearchProviderFailure, ...]


class WorkspaceSearchService:
    """Aggregate enabled and readable providers without cross-domain joins."""

    def __init__(
        self,
        authorization: SearchAuthorizationPolicy,
        providers: tuple[WorkspaceSearchProvider, ...],
    ) -> None:
        keys = tuple(provider.key for provider in providers)
        if any(not key for key in keys):
            raise ValueError("search provider key must not be empty")
        if len(keys) != len(set(keys)):
            raise ValueError("search provider keys must be unique")
        self.authorization = authorization
        # Registration order must not affect the public result projection.
        self.providers = tuple(sorted(providers, key=lambda item: (item.app_key, item.key)))

    def search(
        self,
        context: WorkspaceContext,
        query: str,
        *,
        limit: int = 5,
        offset: int = 0,
    ) -> WorkspaceSearchResponse:
        normalized_query = query.strip()
        if not normalized_query:
            return WorkspaceSearchResponse(query="", groups=(), failures=())
        if limit < 1:
            raise ValueError("limit must be positive")
        if offset < 0:
            raise ValueError("offset must be non-negative")

        # Resolve once.  A stale/revoked context is an authorization failure and
        # must abort the whole request rather than falling back to provider data.
        snapshot = self.authorization.permission_snapshot(context)
        groups: list[SearchGroup] = []
        failures: list[SearchProviderFailure] = []

        for provider in self.providers:
            if not self.authorization.authorize_app(
                context, provider.app_key, snapshot=snapshot
            ):
                continue
            if any(
                not self.authorization.authorize(
                    context, permission, snapshot=snapshot
                )
                for permission in provider.required_permissions
            ):
                continue

            try:
                results = provider.search(
                    context, normalized_query, limit, offset
                )
                if any(item.workspace_id != context.workspace_id for item in results):
                    raise ValueError("provider returned a result from another workspace")
                ranked = tuple(sorted(results, key=_result_rank)[:limit])
            except AuthorizationError:
                # Providers can perform finer resource checks.  Their auth
                # failures are security decisions, never partial-failure data.
                raise
            except Exception:
                failures.append(
                    SearchProviderFailure(provider.key, provider.app_key)
                )
                continue

            groups.append(SearchGroup(provider.key, provider.app_key, ranked))

        return WorkspaceSearchResponse(
            query=normalized_query,
            groups=tuple(groups),
            failures=tuple(failures),
        )


def _result_rank(result: SearchResult) -> tuple[float, str, str, str]:
    """Stable rank: relevance first, then deterministic user-visible keys."""

    return (-result.score, result.title.casefold(), result.resource_id, result.url)


__all__ = [
    "SearchAuthorizationPolicy",
    "SearchGroup",
    "SearchProviderFailure",
    "SearchResult",
    "WorkspaceSearchProvider",
    "WorkspaceSearchResponse",
    "WorkspaceSearchService",
]
