from __future__ import annotations

import io
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from PIL import Image

from access.context import WorkspaceContext
from assets.database import initialize_workspace_database
from assets.router import create_workspace_asset_router
from assets.service import AssetService
from assets.storage import LocalFileStorage


class EmptyMarkdownReader:
    def get_markdown(self, owner_type: str, owner_id: str, field: str):
        return ""


def context(workspace_id: str) -> WorkspaceContext:
    return WorkspaceContext(
        user_id=str(uuid4()),
        workspace_id=workspace_id,
        membership_id=str(uuid4()),
        role_key="owner",
        enabled_app_ids=("assets",),
        authorization_version=1,
    )


def png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (3, 2), (20, 40, 60)).save(output, format="PNG")
    return output.getvalue()


def test_workspace_asset_router_hides_cross_workspace_ids_and_storage(tmp_path):
    path = initialize_workspace_database(tmp_path / "assets.db")
    service = AssetService(
        path,
        storage=LocalFileStorage(tmp_path / "blobs"),
        markdown_source_reader=EmptyMarkdownReader(),
    )

    def require_workspace(workspace_id: str) -> WorkspaceContext:
        return context(workspace_id)

    def csrf(request: Request) -> None:
        if request.headers.get("X-CSRF-Token") != "valid":
            raise HTTPException(status_code=403, detail="CSRF failed")

    app = FastAPI()
    app.include_router(
        create_workspace_asset_router(
            service=service,
            storage=service.storage,
            require_workspace=require_workspace,
            csrf=csrf,
        )
    )
    workspace_a, workspace_b = str(uuid4()), str(uuid4())
    headers = {
        "Content-Type": "image/png",
        "X-File-Name": "asset.png",
        "X-CSRF-Token": "valid",
    }
    with TestClient(app) as client:
        assert client.post(
            f"/api/v1/workspaces/{workspace_a}/assets/images",
            params={"upload_session_id": "session-a"},
            headers={"Content-Type": "image/png", "X-File-Name": "asset.png"},
            content=png_bytes(),
        ).status_code == 403
        first = client.post(
            f"/api/v1/workspaces/{workspace_a}/assets/images",
            params={"upload_session_id": "session-a"},
            headers=headers,
            content=png_bytes(),
        ).json()
        hidden = client.post(
            f"/api/v1/workspaces/{workspace_b}/assets/resolve",
            json={"ids": [first["id"]]},
        ).json()
        assert hidden[0]["exists"] is False
        assert client.get(
            f"/api/v1/workspaces/{workspace_b}/assets/{first['id']}/content"
        ).status_code == 404

        second = client.post(
            f"/api/v1/workspaces/{workspace_b}/assets/images",
            params={"upload_session_id": "session-b"},
            headers=headers,
            content=png_bytes(),
        ).json()
        assert second["id"] != first["id"]
        assert service.get_asset(first["id"], context(workspace_a))["storage_key"] != service.get_asset(
            second["id"], context(workspace_b)
        )["storage_key"]

