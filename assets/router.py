from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Callable
from uuid import UUID
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from access.context import WorkspaceContext
from access.errors import AppNotAvailable, PermissionDenied
from access.service import AuthorizationService

from .service import (
    MAX_UPLOAD_BYTES,
    AssetNotFoundError,
    AssetService,
    InvalidImageError,
    MarkdownSourceNotFoundError,
)
from .storage import LocalFileStorage

SUPPORTED_UPLOAD_MEDIA_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp"}
)


class ResolveAssetsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[Annotated[int, Field(gt=0)]] = Field(
        min_length=1,
        max_length=50,
    )


class ReconcileReferencesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_type: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=128)
    field: str = Field(min_length=1, max_length=64)


def _asset_payload(asset: dict[str, Any]) -> dict[str, Any]:
    asset_id = int(asset["id"])
    return {
        "id": asset_id,
        "exists": True,
        "href": f"/api/assets/{asset_id}/content",
        "media_type": asset["media_type"],
        "byte_size": asset["byte_size"],
    }


def _workspace_asset_payload(
    asset: dict[str, Any], workspace_id: str
) -> dict[str, Any]:
    payload = _asset_payload(asset)
    payload["href"] = (
        f"/api/v1/workspaces/{workspace_id}/assets/{asset['id']}/content"
    )
    return payload


def create_public_asset_router(
    *,
    storage: LocalFileStorage,
    resolve_published_asset: Callable[[str, int], dict[str, Any] | None],
) -> APIRouter:
    """Read-only assets projected by one currently published publication."""
    router = APIRouter()
    api = "/api/publications/public/{publication_public_id}/assets"

    def resolve(publication_public_id: UUID, asset_id: int) -> dict[str, Any] | None:
        return resolve_published_asset(str(publication_public_id), asset_id)

    def payload(publication_public_id: UUID, asset_id: int) -> dict[str, Any]:
        asset = resolve(publication_public_id, asset_id)
        if asset is None:
            return {
                "id": asset_id,
                "exists": False,
                "href": None,
                "media_type": None,
                "byte_size": None,
            }
        return {
            "id": asset_id,
            "exists": True,
            "href": f"{api.format(publication_public_id=publication_public_id)}/{asset_id}/content",
            "media_type": asset["media_type"],
            "byte_size": asset["byte_size"],
        }

    @router.post(f"{api}/resolve")
    async def resolve_assets(
        publication_public_id: UUID,
        request: ResolveAssetsRequest,
    ) -> list[dict[str, Any]]:
        return [payload(publication_public_id, asset_id) for asset_id in request.ids]

    @router.get(f"{api}/{{asset_id}}/content")
    async def get_asset_content(publication_public_id: UUID, asset_id: int) -> Response:
        asset = resolve(publication_public_id, asset_id)
        if asset is None or not storage.exists(asset["storage_key"]):
            raise HTTPException(status_code=404, detail="Asset not found")
        return Response(
            content=storage.open(asset["storage_key"]),
            media_type=asset["media_type"],
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )

    return router


def create_asset_router(
    *,
    service: AssetService,
    storage: LocalFileStorage,
    require_legacy_workspace: Callable[[Request], WorkspaceContext] | None = None,
    csrf: Callable[[Request], None] | None = None,
    authorization: AuthorizationService | None = None,
) -> APIRouter:
    router = APIRouter()

    def context(request: Request) -> WorkspaceContext | None:
        return require_legacy_workspace(request) if require_legacy_workspace else None

    def mutate(request: Request) -> WorkspaceContext | None:
        resolved = context(request)
        if csrf is not None and require_legacy_workspace is not None:
            csrf(request)
        return resolved

    def authorize(
        workspace_context: WorkspaceContext | None, permission: str
    ) -> None:
        if authorization is None or workspace_context is None:
            return
        try:
            authorization.require(
                workspace_context, permission, app_key="assets"
            )
        except AppNotAvailable as error:
            raise HTTPException(status_code=404, detail="Resource not found") from error
        except PermissionDenied as error:
            raise HTTPException(status_code=403, detail="Action forbidden") from error

    @router.post("/api/assets/images", status_code=status.HTTP_201_CREATED)
    async def upload_image(
        request: Request,
        upload_session_id: str = Query(min_length=1, max_length=128),
        x_file_name: str = Header(default="upload", alias="X-File-Name"),
    ) -> dict[str, Any]:
        workspace_context = mutate(request)
        authorize(workspace_context, "assets.create")
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in SUPPORTED_UPLOAD_MEDIA_TYPES:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Only PNG, JPEG, and static WebP images are supported",
            )
        declared_length = request.headers.get("content-length")
        if declared_length is not None:
            try:
                if int(declared_length) > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Image exceeds the upload size limit",
                    )
            except ValueError:
                raise HTTPException(
                    status_code=400,
                    detail="Invalid Content-Length header",
                ) from None
        content_buffer = bytearray()
        async for chunk in request.stream():
            content_buffer.extend(chunk)
            if len(content_buffer) > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="Image exceeds the upload size limit",
                )
        content = bytes(content_buffer)
        try:
            decoded_file_name = unquote(x_file_name, errors="strict")
            asset = service.upload_image(
                content,
                original_name=decoded_file_name,
                upload_session_id=upload_session_id,
                context=workspace_context,
            )
        except (UnicodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except InvalidImageError as error:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=str(error),
            ) from error
        return _asset_payload(asset)

    @router.post("/api/assets/resolve")
    async def resolve_assets(
        request: Request,
        payload: ResolveAssetsRequest,
    ) -> list[dict[str, Any]]:
        workspace_context = context(request)
        authorize(workspace_context, "assets.read_all")
        results: list[dict[str, Any]] = []
        for asset_id in payload.ids:
            try:
                results.append(
                    _asset_payload(service.get_asset(asset_id, workspace_context))
                )
            except AssetNotFoundError:
                results.append(
                    {
                        "id": asset_id,
                        "exists": False,
                        "href": None,
                        "media_type": None,
                        "byte_size": None,
                    }
                )
        return results

    @router.post("/api/assets/references/reconcile")
    async def reconcile_references(
        request: Request,
        payload: ReconcileReferencesRequest,
    ) -> dict[str, Any]:
        workspace_context = mutate(request)
        authorize(workspace_context, "assets.update_all")
        try:
            asset_ids = service.reconcile(
                owner_type=payload.owner_type,
                owner_id=payload.owner_id,
                field=payload.field,
                context=workspace_context,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except MarkdownSourceNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except AssetNotFoundError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "owner_type": payload.owner_type,
            "owner_id": payload.owner_id,
            "field": payload.field,
            "asset_ids": list(asset_ids),
        }

    @router.get("/api/assets/{asset_id}/content")
    async def get_asset_content(request: Request, asset_id: int) -> Response:
        workspace_context = context(request)
        authorize(workspace_context, "assets.read_all")
        try:
            asset = service.get_asset(asset_id, workspace_context)
        except AssetNotFoundError as error:
            raise HTTPException(status_code=404, detail="Asset not found") from error
        if not storage.exists(asset["storage_key"]):
            raise HTTPException(status_code=404, detail="Asset content not found")
        return Response(
            content=storage.open(asset["storage_key"]),
            media_type=asset["media_type"],
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )

    return router


def create_workspace_asset_router(
    *,
    service: AssetService,
    storage: LocalFileStorage,
    require_workspace: Callable[..., WorkspaceContext],
    csrf: Callable[[Request], None] | None = None,
    authorization: AuthorizationService | None = None,
) -> APIRouter:
    """Authenticated, tenant-scoped Asset API used by Workspace apps."""
    router = APIRouter()
    api = "/api/v1/workspaces/{workspace_id}/assets"

    def mutate(request: Request) -> None:
        if csrf is not None:
            csrf(request)

    def require(context: WorkspaceContext, permission: str) -> None:
        if authorization is None:
            return
        try:
            authorization.require(context, permission, app_key="assets")
        except AppNotAvailable as error:
            raise HTTPException(status_code=404, detail="Resource not found") from error
        except PermissionDenied as error:
            raise HTTPException(status_code=403, detail="Action forbidden") from error

    @router.post(f"{api}/images", status_code=status.HTTP_201_CREATED)
    async def upload_image(
        request: Request,
        upload_session_id: str = Query(min_length=1, max_length=128),
        x_file_name: str = Header(default="upload", alias="X-File-Name"),
        context: WorkspaceContext = Depends(require_workspace),
    ) -> dict[str, Any]:
        require(context, "assets.create")
        mutate(request)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if content_type not in SUPPORTED_UPLOAD_MEDIA_TYPES:
            raise HTTPException(status_code=415, detail="Only PNG, JPEG, and static WebP images are supported")
        declared_length = request.headers.get("content-length")
        if declared_length is not None:
            try:
                if int(declared_length) > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Image exceeds the upload size limit")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid Content-Length header") from None
        content_buffer = bytearray()
        async for chunk in request.stream():
            content_buffer.extend(chunk)
            if len(content_buffer) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Image exceeds the upload size limit")
        try:
            asset = service.upload_image(
                bytes(content_buffer),
                original_name=unquote(x_file_name, errors="strict"),
                upload_session_id=upload_session_id,
                context=context,
            )
        except (UnicodeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except InvalidImageError as error:
            raise HTTPException(status_code=415, detail=str(error)) from error
        return _workspace_asset_payload(asset, context.workspace_id)

    @router.post(f"{api}/resolve")
    async def resolve_assets(
        payload: ResolveAssetsRequest,
        context: WorkspaceContext = Depends(require_workspace),
    ) -> list[dict[str, Any]]:
        require(context, "assets.read_all")
        results: list[dict[str, Any]] = []
        for asset_id in payload.ids:
            try:
                results.append(
                    _workspace_asset_payload(
                        service.get_asset(asset_id, context), context.workspace_id
                    )
                )
            except AssetNotFoundError:
                results.append({"id": asset_id, "exists": False, "href": None, "media_type": None, "byte_size": None})
        return results

    @router.post(f"{api}/references/reconcile")
    async def reconcile_references(
        request: Request,
        payload: ReconcileReferencesRequest,
        context: WorkspaceContext = Depends(require_workspace),
    ) -> dict[str, Any]:
        require(context, "assets.update_all")
        mutate(request)
        try:
            asset_ids = service.reconcile(
                owner_type=payload.owner_type,
                owner_id=payload.owner_id,
                field=payload.field,
                context=context,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except MarkdownSourceNotFoundError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except AssetNotFoundError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"owner_type": payload.owner_type, "owner_id": payload.owner_id, "field": payload.field, "asset_ids": list(asset_ids)}

    @router.get(f"{api}/{{asset_id}}/content")
    async def get_asset_content(
        asset_id: int,
        context: WorkspaceContext = Depends(require_workspace),
    ) -> Response:
        require(context, "assets.read_all")
        try:
            asset = service.get_asset(asset_id, context)
        except AssetNotFoundError as error:
            raise HTTPException(status_code=404, detail="Asset not found") from error
        if not storage.exists(asset["storage_key"]):
            raise HTTPException(status_code=404, detail="Asset content not found")
        return Response(
            content=storage.open(asset["storage_key"]),
            media_type=asset["media_type"],
            headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=31536000, immutable"},
        )

    return router
