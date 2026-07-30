from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

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


def create_asset_router(
    *,
    service: AssetService,
    storage: LocalFileStorage,
) -> APIRouter:
    router = APIRouter()

    @router.post("/api/assets/images", status_code=status.HTTP_201_CREATED)
    async def upload_image(
        request: Request,
        upload_session_id: str = Query(min_length=1, max_length=128),
        x_file_name: str = Header(default="upload", alias="X-File-Name"),
    ) -> dict[str, Any]:
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
            asset = service.upload_image(
                content,
                original_name=x_file_name,
                upload_session_id=upload_session_id,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except InvalidImageError as error:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=str(error),
            ) from error
        return _asset_payload(asset)

    @router.post("/api/assets/resolve")
    async def resolve_assets(
        payload: ResolveAssetsRequest,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for asset_id in payload.ids:
            try:
                results.append(_asset_payload(service.get_asset(asset_id)))
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
        payload: ReconcileReferencesRequest,
    ) -> dict[str, Any]:
        try:
            asset_ids = service.reconcile(
                owner_type=payload.owner_type,
                owner_id=payload.owner_id,
                field=payload.field,
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
    async def get_asset_content(asset_id: int) -> Response:
        try:
            asset = service.get_asset(asset_id)
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
