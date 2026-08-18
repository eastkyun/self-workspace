from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from access.context import WorkspaceContext
from access.errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from access.service import AuthorizationService
from shared.app_registry import AppRegistry
from shared.templating import (
    authorized_workspace_shell_context,
    create_templates,
    shell_context,
)
from workspace_platform.effective_apps import EffectiveWorkspaceApps

from .application import JobApplicationService
from .errors import JobConflict, JobNotFound, JobValidationError
from .schemas import JobSchedule, ScheduleKind

DIRECTORY = Path(__file__).resolve().parent
STATIC_DIR = DIRECTORY / "static"
templates = create_templates(DIRECTORY / "templates")


class SchedulePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: ScheduleKind
    expression: str | None = None
    timezone: str | None = None

    def value(self) -> JobSchedule:
        return JobSchedule(self.kind, self.expression, self.timezone)


class CreateJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    job_type: str = Field(min_length=1, max_length=64)
    schedule: SchedulePayload
    type_config: dict[str, Any]


class UpdateJobPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    schedule: SchedulePayload
    type_config: dict[str, Any]
    expected_revision: int = Field(ge=1)


class TypePreviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type_config: dict[str, Any]


class FormatterProfilePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    instructions: str = Field(min_length=1, max_length=30_000)
    model: str = Field(min_length=1, max_length=120)


class UpdateFormatterProfilePayload(FormatterProfilePayload):
    expected_revision: int = Field(ge=1)


def _error(error: Exception) -> HTTPException:
    if isinstance(error, JobNotFound):
        return HTTPException(status_code=404, detail=str(error) or "Resource not found")
    if isinstance(error, JobConflict):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, (JobValidationError, ValueError)):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=500, detail="Job operation failed")


def create_jobs_router(
    *,
    service: JobApplicationService,
    authorization: AuthorizationService,
    require_workspace: Callable,
    csrf: Callable[[Request], None],
    csrf_cookie_name: str,
    app_registry: AppRegistry,
    effective_apps: EffectiveWorkspaceApps | None = None,
    formatter_profiles: Any | None = None,
) -> APIRouter:
    router = APIRouter()
    api = "/api/v1/workspaces/{workspace_id}/jobs"

    def require_read(context: WorkspaceContext):
        return authorization.require(context, "jobs.read_all", app_key="jobs")

    def require_create(context: WorkspaceContext) -> None:
        snapshot = require_read(context)
        if not authorization.authorize(context, "jobs.create", snapshot=snapshot):
            raise PermissionDenied("permission denied")

    def require_formatter_manage(context: WorkspaceContext) -> None:
        snapshot = require_read(context)
        if not authorization.authorize(context, "jobs.manage", snapshot=snapshot):
            raise PermissionDenied("permission denied")

    def require_resource_action(
        context: WorkspaceContext, job: dict[str, Any], action: str
    ) -> None:
        snapshot = require_read(context)
        if authorization.authorize(context, f"jobs.{action}_all", snapshot=snapshot):
            return
        if authorization.authorize(
            context, f"jobs.{action}_own", job["created_by"], snapshot=snapshot
        ):
            return
        raise PermissionDenied("permission denied")

    @router.get("/w/{workspace_id}/jobs", include_in_schema=False)
    async def jobs_page(
        request: Request,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        require_read(context)
        if effective_apps is not None:
            view = authorized_workspace_shell_context(
                request,
                app_registry,
                current_app_id="jobs",
                page_title="Jobs",
                workspace_context=context,
                effective_apps=effective_apps,
            )
        else:
            view = shell_context(
                request,
                app_registry,
                current_app_id="jobs",
                page_title="Jobs",
                workspace_id=context.workspace_id,
            )
        view["jobs_api_base"] = f"/api/v1/workspaces/{context.workspace_id}/jobs"
        view["jobs_csrf_cookie_name"] = csrf_cookie_name
        return templates.TemplateResponse(request, "jobs.html", view)

    @router.get(f"{api}/types")
    async def list_types(context: WorkspaceContext = Depends(require_workspace)):
        require_read(context)
        return service.list_types()

    @router.get(f"{api}/formatters")
    async def list_formatters(
        context: WorkspaceContext = Depends(require_workspace),
    ):
        require_read(context)
        return formatter_profiles.list(context.workspace_id) if formatter_profiles else []

    @router.get(f"{api}/formatters/{{profile_id}}")
    async def get_formatter(
        profile_id: str,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        require_read(context)
        if formatter_profiles is None:
            raise HTTPException(status_code=404, detail="Formatter profiles unavailable")
        try:
            return formatter_profiles.get(context.workspace_id, profile_id)
        except Exception as error:
            raise _error(error) from error

    @router.post(f"{api}/formatters", status_code=status.HTTP_201_CREATED)
    async def create_formatter(
        request: Request,
        payload: FormatterProfilePayload,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        csrf(request)
        require_formatter_manage(context)
        if formatter_profiles is None:
            raise HTTPException(status_code=404, detail="Formatter profiles unavailable")
        try:
            return formatter_profiles.create(
                context.workspace_id,
                context.user_id,
                name=payload.name,
                instructions=payload.instructions,
                model=payload.model,
            )
        except Exception as error:
            raise _error(error) from error

    @router.put(f"{api}/formatters/{{profile_id}}")
    async def update_formatter(
        profile_id: str,
        request: Request,
        payload: UpdateFormatterProfilePayload,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        csrf(request)
        require_formatter_manage(context)
        if formatter_profiles is None:
            raise HTTPException(status_code=404, detail="Formatter profiles unavailable")
        try:
            return formatter_profiles.update(
                context.workspace_id,
                profile_id,
                context.user_id,
                name=payload.name,
                instructions=payload.instructions,
                model=payload.model,
                expected_revision=payload.expected_revision,
            )
        except Exception as error:
            raise _error(error) from error

    @router.delete(f"{api}/formatters/{{profile_id}}")
    async def delete_formatter(
        profile_id: str,
        request: Request,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        csrf(request)
        require_formatter_manage(context)
        if formatter_profiles is None:
            raise HTTPException(status_code=404, detail="Formatter profiles unavailable")
        try:
            return formatter_profiles.delete(
                context.workspace_id, profile_id, context.user_id
            )
        except Exception as error:
            raise _error(error) from error

    @router.post(f"{api}/types/{{job_type}}/preview")
    async def preview_type(
        job_type: str,
        request: Request,
        payload: TypePreviewPayload,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        csrf(request)
        require_create(context)
        try:
            return await service.preview_type(job_type, context, payload.type_config)
        except Exception as error:
            raise _error(error) from error

    @router.get(api)
    async def list_jobs(context: WorkspaceContext = Depends(require_workspace)):
        require_read(context)
        return service.list_jobs(context.workspace_id)

    @router.get(f"{api}/health")
    async def jobs_health(context: WorkspaceContext = Depends(require_workspace)):
        require_read(context)
        return service.health()

    @router.get(f"{api}/{{job_id}}")
    async def get_job(job_id: str, context: WorkspaceContext = Depends(require_workspace)):
        require_read(context)
        try:
            return service.get_job(context.workspace_id, job_id)
        except Exception as error:
            raise _error(error) from error

    @router.post(api, status_code=status.HTTP_201_CREATED)
    async def create_job(
        request: Request,
        payload: CreateJobPayload,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        csrf(request)
        require_create(context)
        try:
            return service.create_job(
                workspace_id=context.workspace_id,
                actor_id=context.user_id,
                name=payload.name,
                job_type=payload.job_type,
                schedule=payload.schedule.value(),
                type_config=payload.type_config,
            )
        except Exception as error:
            raise _error(error) from error

    @router.put(f"{api}/{{job_id}}")
    async def update_job(
        job_id: str,
        request: Request,
        payload: UpdateJobPayload,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        csrf(request)
        try:
            job = service.get_job(context.workspace_id, job_id)
            require_resource_action(context, job, "update")
            return service.update_job(
                workspace_id=context.workspace_id,
                job_id=job_id,
                actor_id=context.user_id,
                name=payload.name,
                schedule=payload.schedule.value(),
                type_config=payload.type_config,
                expected_revision=payload.expected_revision,
            )
        except Exception as error:
            raise _error(error) from error

    def mutation_job(
        request: Request,
        job_id: str,
        context: WorkspaceContext,
        action: str = "update",
    ) -> dict[str, Any]:
        csrf(request)
        job = service.get_job(context.workspace_id, job_id)
        require_resource_action(context, job, action)
        return job

    @router.post(f"{api}/{{job_id}}/test", status_code=status.HTTP_202_ACCEPTED)
    async def test_job(
        job_id: str,
        request: Request,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        try:
            mutation_job(request, job_id, context)
            return service.enqueue_test(context.workspace_id, job_id)
        except Exception as error:
            raise _error(error) from error

    @router.post(f"{api}/{{job_id}}/activate")
    async def activate(job_id: str, request: Request, context: WorkspaceContext = Depends(require_workspace)):
        try:
            mutation_job(request, job_id, context)
            return service.repository.activate(context.workspace_id, job_id, context.user_id)
        except Exception as error:
            raise _error(error) from error

    @router.post(f"{api}/{{job_id}}/pause")
    async def pause(job_id: str, request: Request, context: WorkspaceContext = Depends(require_workspace)):
        try:
            mutation_job(request, job_id, context)
            return service.repository.pause(context.workspace_id, job_id, context.user_id)
        except Exception as error:
            raise _error(error) from error

    @router.post(f"{api}/{{job_id}}/run", status_code=status.HTTP_202_ACCEPTED)
    async def run(job_id: str, request: Request, context: WorkspaceContext = Depends(require_workspace)):
        try:
            mutation_job(request, job_id, context)
            return service.repository.enqueue_manual(context.workspace_id, job_id)
        except Exception as error:
            raise _error(error) from error

    @router.delete(f"{api}/{{job_id}}")
    async def archive(job_id: str, request: Request, context: WorkspaceContext = Depends(require_workspace)):
        try:
            mutation_job(request, job_id, context, "delete")
            return service.repository.archive(context.workspace_id, job_id, context.user_id)
        except Exception as error:
            raise _error(error) from error

    @router.delete(f"{api}/{{job_id}}/permanent")
    async def delete_archived_job(
        job_id: str,
        request: Request,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        try:
            mutation_job(request, job_id, context, "delete")
            deleted = service.repository.delete_archived(context.workspace_id, job_id)
            return {"deleted": True, "id": deleted["id"]}
        except Exception as error:
            raise _error(error) from error

    @router.get(f"{api}/{{job_id}}/runs")
    async def runs(job_id: str, context: WorkspaceContext = Depends(require_workspace)):
        require_read(context)
        try:
            return service.repository.list_run_details(context.workspace_id, job_id)
        except Exception as error:
            raise _error(error) from error

    @router.delete(f"{api}/{{job_id}}/runs/{{run_id}}")
    async def delete_run_history(
        job_id: str,
        run_id: str,
        request: Request,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        try:
            mutation_job(request, job_id, context, "delete")
            return service.repository.delete_run_history(
                context.workspace_id, job_id, run_id
            )
        except Exception as error:
            raise _error(error) from error

    @router.post(
        f"{api}/{{job_id}}/runs/{{run_id}}/retry",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def retry_run(
        job_id: str,
        run_id: str,
        request: Request,
        context: WorkspaceContext = Depends(require_workspace),
    ):
        try:
            mutation_job(request, job_id, context)
            return service.repository.enqueue_retry(
                context.workspace_id, job_id, run_id
            )
        except Exception as error:
            raise _error(error) from error

    return router
