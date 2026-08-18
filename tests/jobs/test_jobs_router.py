from __future__ import annotations

from fastapi import FastAPI
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx

from access.context import WorkspaceContext
from job_platform.application import JobApplicationService
from job_platform.handlers import JobTypeRegistry
from job_platform.handlers import JobOutcome
from job_platform.repository import JobRepository
from job_platform.router import create_jobs_router
from job_platform.database import initialize_database
from job_platform.schemas import JobSchedule, RunStatus, ScheduleKind
from web_collector.adapter import WebCollectionAdapter
from web_collector.database import initialize_database as initialize_web_collection_database
from web_collector.repository import FormatterProfileRepository
from shared.app_registry import AppRegistry


CONTEXT = WorkspaceContext(
    user_id="user-a", workspace_id="workspace-a", membership_id="member-a",
    role_key="Owner", enabled_app_ids=("jobs", "notes"), authorization_version=1,
)


class AllowAuthorization:
    def require(self, *_args, **_kwargs):
        return object()

    def authorize(self, *_args, **_kwargs):
        return True

    def permission_snapshot(self, *_args, **_kwargs):
        return object()


class EffectiveApps:
    authorization = AllowAuthorization()

    def list(self, *_args, **_kwargs):
        return (
            SimpleNamespace(
                key="notes", route="/w/{workspace_id}/notes",
                icon="/static/icons/notes.svg", display_name="Notes",
                sidebar_visible=True, launcher_visible=True,
            ),
            SimpleNamespace(
                key="jobs", route="/w/{workspace_id}/jobs",
                icon="/static/icons/jobs.svg", display_name="Jobs",
                sidebar_visible=True, launcher_visible=True,
            ),
        )


def build_client(tmp_path, *, effective_apps=None):
    database = initialize_database(tmp_path / "jobs.db")
    initialize_web_collection_database(database)
    registry = JobTypeRegistry([WebCollectionAdapter()])
    service = JobApplicationService(JobRepository(database, registry), registry)
    app = FastAPI()
    app.state.jobs_repository = service.repository

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
            app_registry=AppRegistry.load_default(),
            effective_apps=effective_apps,
            formatter_profiles=FormatterProfileRepository(database),
        )
    )
    return app


def test_formatter_profile_crud_is_workspace_scoped_and_protects_in_use_profiles(tmp_path):
    async def scenario():
        app = build_client(tmp_path)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/v1/workspaces/workspace-a/jobs/formatters",
                json={
                    "name": "시장 요약",
                    "instructions": "수집된 값만 사용해 Markdown으로 정리한다.",
                    "model": "gpt-5.6-luna",
                },
            )
            assert created.status_code == 201
            profile = created.json()
            assert profile["revision"] == 1
            assert profile["use_count"] == 0

            listing = await client.get(
                "/api/v1/workspaces/workspace-a/jobs/formatters"
            )
            listed = listing.json()
            assert listed[0]["id"] == "builtin-financial-market-briefing"
            assert listed[0]["is_builtin"] is True
            assert [item["id"] for item in listed[1:]] == [profile["id"]]

            builtin_changed = await client.put(
                "/api/v1/workspaces/workspace-a/jobs/formatters/builtin-financial-market-briefing",
                json={
                    "name": "맞춤 금융 브리핑",
                    "instructions": "원문 수치만 사용해 금융시장 Markdown으로 정리한다.",
                    "model": "gpt-5.6-luna",
                    "expected_revision": 1,
                },
            )
            assert builtin_changed.status_code == 200
            assert builtin_changed.json()["is_overridden"] is True
            builtin_reset = await client.delete(
                "/api/v1/workspaces/workspace-a/jobs/formatters/builtin-financial-market-briefing"
            )
            assert builtin_reset.status_code == 200
            assert builtin_reset.json()["reset"] is True

            updated = await client.put(
                f"/api/v1/workspaces/workspace-a/jobs/formatters/{profile['id']}",
                json={
                    "name": "시장 요약 v2",
                    "instructions": "표와 요약으로만 정리한다.",
                    "model": "gpt-5.6-luna",
                    "expected_revision": 1,
                },
            )
            assert updated.status_code == 200
            assert updated.json()["revision"] == 2

            disposable = await client.post(
                "/api/v1/workspaces/workspace-a/jobs/formatters",
                json={
                    "name": "임시 요약",
                    "instructions": "한 문단으로 정리한다.",
                    "model": "gpt-5.6-luna",
                },
            )
            removed = await client.delete(
                f"/api/v1/workspaces/workspace-a/jobs/formatters/{disposable.json()['id']}"
            )
            assert removed.status_code == 200
            assert removed.json()["deleted"] is True

            job = await client.post(
                "/api/v1/workspaces/workspace-a/jobs",
                json={
                    "name": "시장 수집",
                    "job_type": "WEB_COLLECTION",
                    "schedule": {"kind": "MANUAL", "expression": None, "timezone": None},
                    "type_config": {
                        "url": "https://example.com",
                        "selector": "main",
                        "output_format": "FINANCIAL_MARKET_BRIEFING",
                        "formatter_profile_id": profile["id"],
                    },
                },
            )
            assert job.status_code == 201
            assert job.json()["type_config"]["formatter_profile_id"] == profile["id"]

            blocked = await client.delete(
                f"/api/v1/workspaces/workspace-a/jobs/formatters/{profile['id']}"
            )
            assert blocked.status_code == 409
            assert "used by a Job" in blocked.json()["detail"]

    asyncio.run(scenario())


def test_retry_endpoint_queues_failed_run_with_csrf_protection(tmp_path):
    async def scenario():
        app = build_client(tmp_path)
        repository = app.state.jobs_repository
        now = datetime(2026, 8, 14, tzinfo=UTC)
        job = repository.create_job(
            workspace_id="workspace-a",
            actor_id="user-a",
            name="retry",
            job_type="WEB_COLLECTION",
            schedule=JobSchedule(ScheduleKind.MANUAL),
            type_config={"url": "https://example.com", "selector": "main"},
            now=now,
        )
        repository.mark_tested("workspace-a", job["id"], 1, now=now)
        repository.activate("workspace-a", job["id"], "user-a", now=now)
        run = repository.enqueue_manual("workspace-a", job["id"], now=now)
        claim = repository.claim_next("worker-a", now=now)
        repository.complete(
            run["id"],
            claim["lease_token"],
            JobOutcome(RunStatus.FAILED, "FETCH_FAILED"),
            error_code="FETCH_FAILED",
            now=now,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.post(
                f"/api/v1/workspaces/workspace-a/jobs/{job['id']}/runs/{run['id']}/retry"
            )

        assert response.status_code == 202
        assert response.json()["trigger_kind"] == "RETRY"
        assert response.json()["source_run_id"] == run["id"]

    asyncio.run(scenario())


def test_jobs_page_keeps_workspace_enabled_jobs_in_navigation(tmp_path):
    async def scenario():
        transport = httpx.ASGITransport(
            app=build_client(tmp_path, effective_apps=EffectiveApps())
        )
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get("/w/workspace-a/jobs")

        assert response.status_code == 200
        assert response.text.count('data-app-id="jobs"') == 2
        assert 'href="/w/workspace-a/jobs"' in response.text
        assert 'src="/static/icons/jobs.svg"' in response.text

    asyncio.run(scenario())


def test_terminal_run_history_can_be_deleted(tmp_path):
    async def scenario():
        app = build_client(tmp_path)
        repository = app.state.jobs_repository
        now = datetime(2026, 8, 14, tzinfo=UTC)
        job = repository.create_job(
            workspace_id="workspace-a", actor_id="user-a", name="cleanup",
            job_type="WEB_COLLECTION", schedule=JobSchedule(ScheduleKind.MANUAL),
            type_config={"url": "https://example.com", "selector": "main"},
            now=now,
        )
        repository.mark_tested("workspace-a", job["id"], 1, now=now)
        repository.activate("workspace-a", job["id"], "user-a", now=now)
        run = repository.enqueue_manual("workspace-a", job["id"], now=now)
        claim = repository.claim_next("worker-a", now=now)
        repository.complete(
            run["id"], claim["lease_token"],
            JobOutcome(RunStatus.FAILED, "FETCH_FAILED"), now=now,
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.delete(
                f"/api/v1/workspaces/workspace-a/jobs/{job['id']}/runs/{run['id']}"
            )
        assert response.status_code == 200
        assert response.json()["deleted_count"] == 1
        assert repository.list_runs("workspace-a", job["id"]) == []

    asyncio.run(scenario())


def test_jobs_api_exposes_types_and_draft_crud(tmp_path):
    async def scenario():
        transport = httpx.ASGITransport(app=build_client(tmp_path))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            types = await client.get("/api/v1/workspaces/workspace-a/jobs/types")
            assert types.status_code == 200
            assert types.json()[0]["editor_key"] == "web-collection"

            health = await client.get("/api/v1/workspaces/workspace-a/jobs/health")
            assert health.status_code == 200
            assert health.json()["worker"] == {
                "availability": "unknown",
                "status": "unknown",
                "last_heartbeat_at": None,
                "version": None,
            }

            preview = await client.post(
                "/api/v1/workspaces/workspace-a/jobs/types/WEB_COLLECTION/preview",
                json={"type_config": {"url": "https://example.com/notices"}},
            )
            assert preview.status_code == 200
            assert preview.json() == {
                "success": False,
                "code": "BROWSER_UNAVAILABLE",
            }

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

            updated = await client.put(
                f"/api/v1/workspaces/workspace-a/jobs/{detail['id']}",
                json={
                    "name": "공지 수집 - 수정",
                    "schedule": {"kind": "MANUAL", "expression": None, "timezone": None},
                    "type_config": {
                        "url": "https://example.com/updated", "selector": "main",
                        "note_policy": "CHANGED_ONLY", "extract_links": True,
                        "extract_images": False,
                    },
                    "expected_revision": 1,
                },
            )
            assert updated.status_code == 200
            assert updated.json()["name"] == "공지 수집 - 수정"
            assert updated.json()["definition_revision"] == 2
            assert updated.json()["type_config"]["url"] == "https://example.com/updated"

            tested = await client.post(f"/api/v1/workspaces/workspace-a/jobs/{detail['id']}/test")
            assert tested.status_code == 202
            assert tested.json()["status"] == "QUEUED"
            assert tested.json()["dedupe_key"].startswith("test:")
            activated = await client.post(f"/api/v1/workspaces/workspace-a/jobs/{detail['id']}/activate")
            assert activated.status_code == 409

            archived = await client.delete(f"/api/v1/workspaces/workspace-a/jobs/{detail['id']}")
            assert archived.status_code == 200
            assert archived.json()["status"] == "ARCHIVED"

            deleted = await client.delete(
                f"/api/v1/workspaces/workspace-a/jobs/{detail['id']}/permanent"
            )
            assert deleted.status_code == 200
            assert deleted.json() == {"deleted": True, "id": detail["id"]}
            missing = await client.get(
                f"/api/v1/workspaces/workspace-a/jobs/{detail['id']}"
            )
            assert missing.status_code == 404

    asyncio.run(scenario())
