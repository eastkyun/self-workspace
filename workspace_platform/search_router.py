"""Authenticated API adapter for workspace-wide search."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, Query

from access.context import WorkspaceContext
from access.errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from access.service import AuthorizationService
from shared.errors import APIError

from .search import WorkspaceSearchService


def create_workspace_search_router(
    service: WorkspaceSearchService,
    authorization: AuthorizationService,
    require_workspace: Callable[..., WorkspaceContext],
) -> APIRouter:
    router = APIRouter(prefix="/api/v1/workspaces/{workspace_id}")

    @router.get("/search")
    def search_workspace(
        q: Annotated[str, Query(max_length=200)] = "",
        limit: Annotated[int, Query(ge=1, le=50)] = 5,
        offset: Annotated[int, Query(ge=0)] = 0,
        context: WorkspaceContext = Depends(require_workspace),
    ) -> dict[str, object]:
        try:
            authorization.require(
                context,
                "search.read_all",
                app_key="search",
            )
            return asdict(service.search(context, q, limit=limit, offset=offset))
        except WorkspaceNotAvailable as error:
            raise APIError(status_code=404, code="workspace_not_found", message="Resource not found") from error
        except AppNotAvailable as error:
            raise APIError(status_code=404, code="app_not_found", message="Resource not found") from error
        except PermissionDenied as error:
            raise APIError(status_code=403, code="permission_denied", message="Action forbidden") from error

    return router


__all__ = ["create_workspace_search_router"]
