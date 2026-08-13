from __future__ import annotations

from fastapi import FastAPI
import asyncio

import httpx

from access.context import WorkspaceContext
from job_platform.application import JobApplicationService
from job_platform.handlers import JobTypeRegistry
from job_platform.repository import JobRepository
from job_platform.router import create_jobs_router
from job_platform.database import initialize_database
from web_collector.adapter import WebCollectionAdapter
from web_collector.database import initialize_database as initialize_web_collection_database


CONTEXT = WorkspaceContext(
    user_id="user-a", workspace_id="workspace-a", membership_id="member-a",
    role_key="Owner", enabled_app_ids=("jobs", "notes"), authorization_version=1,
)


class AllowAuthorization:
    def require(self, *_args, **_kwargs):
        return object()

    def authorize(self, *_args, **_kwargs):
        return True


def build_client(tmp_path):
    database = initialize_database(tmp_path / "jobs.db")
    initialize_web_collection_database(database)
    registry = JobTypeRegistry([WebCollectionAdapter()])
    service = JobApplicationService(JobRepository(database, registry), registry)
    app = FastAPI()

    async def require_workspace(workspace_id: str):
        assert workspace_id == CONTEXT.workspace_id
        return CONTEXT

    app.include_router(
        create_jobs_router(
            service=service,
            authorization=AllowAuthorization(),
            require_workspace=require_workspace,
            csrf=lambda _request: None,
            csrf_cookie_name="csrf",
            app_registry=None,
        )
    )
    return app


def test_jobs_api_exposes_types_and_draft_crud(tmp_path):
    async def scenario():
        transport = httpx.ASGITransport(app=build_client(tmp_path))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            types = await client.get("/api/v1/workspaces/workspace-a/jobs/types")
            assert types.status_code == 200
            assert types.json()[0]["editor_key"] == "web-collection"

            created = await client.post(
                "/api/v1/workspaces/workspace-a/jobs",
                json={
                    "name": "공지 수집",
                    "job_type": "WEB_COLLECTION",
                    "schedule": {"kind": "INTERVAL", "expression": "300", "timezone": None},
                    "type_config": {
                        "url": "https://example.com/notices", "selector": "#notices",
                        "note_policy": "CHANGED_ONLY", "extract_links": True,
                        "extract_images": False,
                    },
                },
            )
            assert created.status_code == 201
            detail = created.json()
            assert detail["status"] == "DRAFT"
            assert detail["type_config"]["selector"] == "#notices"

            listing = await client.get("/api/v1/workspaces/workspace-a/jobs")
            assert [item["id"] for item in listing.json()] == [detail["id"]]

            tested = await client.post(f"/api/v1/workspaces/workspace-a/jobs/{detail['id']}/test")
            assert tested.status_code == 200
            assert tested.json() == {"success": False, "code": "BROWSER_UNAVAILABLE", "diagnostics": {}}
            activated = await client.post(f"/api/v1/workspaces/workspace-a/jobs/{detail['id']}/activate")
            assert activated.status_code == 409

    asyncio.run(scenario())
