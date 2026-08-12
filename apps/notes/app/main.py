from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from access.context import PermissionSnapshot, WorkspaceContext
from access.errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from apps.notes.app.database import initialize_database, resolve_database_path, verify_database
from apps.notes.app.schemas import (
    NoteCreate, NoteLinkResponse, NoteResponse, NoteType, NoteUpdate, SourceReferenceInput,
)
from apps.notes.app.service import (
    DuplicateLinkError, LinkNotFoundError, NoteNotFoundError, NotesService,
    SourceNotFoundError,
)
from shared.app_registry import AppRegistry, PORTAL_STATIC_DIR
from shared.errors import APIError
from shared.source_references import SourceReferenceResolver
from shared.templating import SHARED_STATIC_DIR, create_templates, shell_context

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"
templates = create_templates(TEMPLATES_DIR)

ContextDependency = Callable[..., WorkspaceContext]
CsrfDependency = Callable[[Request], None]


def _translate(error: Exception) -> HTTPException:
    if isinstance(error, (WorkspaceNotAvailable, AppNotAvailable)):
        return HTTPException(status_code=404, detail="Resource not found")
    if isinstance(error, PermissionDenied):
        return HTTPException(status_code=403, detail="Action forbidden")
    if isinstance(error, (NoteNotFoundError, LinkNotFoundError)):
        return HTTPException(status_code=404, detail="Note not found")
    if isinstance(error, SourceNotFoundError):
        return HTTPException(status_code=404, detail="Source not found")
    if isinstance(error, DuplicateLinkError):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail=str(error))
    raise error


def create_notes_router(
    *,
    service: NotesService,
    app_registry: AppRegistry,
    require_workspace: ContextDependency,
    csrf: CsrfDependency | None = None,
    csrf_cookie_name: str | None = None,
) -> APIRouter:
    """Canonical workspace Notes UI/API router.

    ``require_workspace`` must authenticate and resolve the path workspace on
    every request. Authorization remains in ``NotesService`` so resource-owner
    checks cannot be bypassed by calling the service outside HTTP.
    """
    def require_v2_schema() -> None:
        try:
            verify_database(service.database_path, require_current=True)
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail="Notes workspace migration is pending") from error

    router = APIRouter(dependencies=[Depends(require_v2_schema)])

    def mutate(request: Request) -> None:
        if csrf is not None:
            csrf(request)

    def render(request: Request, workspace_id: str, mode: str):
        canonical_routes = {
            "/": f"/w/{workspace_id}/home",
            "/apps/notes/": f"/w/{workspace_id}/notes",
            "/apps/calendar/": f"/w/{workspace_id}/calendar",
            "/apps/publishing/": f"/w/{workspace_id}/publications",
        }
        return templates.TemplateResponse(
            request,
            "notes.html",
            shell_context(
                request,
                app_registry,
                current_app_id="notes",
                page_title="Notes",
                path_for=lambda path: canonical_routes.get(path, path),
            )
            | {
                "notes_mode": mode,
                "notes_api_base": f"/api/v1/workspaces/{workspace_id}/notes",
                "notes_internal_link_endpoint": f"/api/v1/workspaces/{workspace_id}/internal-links/resolve",
                "notes_assets_api_base": f"/api/v1/workspaces/{workspace_id}/assets",
                "notes_publications_api_base": f"/api/v1/workspaces/{workspace_id}/publications",
                "notes_publication_types_endpoint": f"/api/v1/workspaces/{workspace_id}/publications/types",
                "notes_csrf_cookie_name": csrf_cookie_name or "",
                "workspace_user_state_api": f"/api/v1/workspaces/{workspace_id}",
                "notes_workspace_route": f"/w/{workspace_id}/notes",
            },
        )

    @router.get("/w/{workspace_id}/notes", include_in_schema=False)
    def index(request: Request, workspace_id: str, context: WorkspaceContext = Depends(require_workspace)):
        try:
            service.authorization.require(context, "notes.read_all", app_key="notes")
        except Exception as error:
            raise _translate(error) from error
        return render(request, workspace_id, "browse")

    @router.get("/w/{workspace_id}/notes/new", include_in_schema=False)
    def new_note(request: Request, workspace_id: str, context: WorkspaceContext = Depends(require_workspace)):
        try:
            snapshot = service.authorization.require(
                context, "notes.read_all", app_key="notes"
            )
            if not service.authorization.authorize(context, "notes.create", snapshot=snapshot):
                raise PermissionDenied("permission denied")
        except Exception as error:
            raise _translate(error) from error
        return render(request, workspace_id, "new")

    api = "/api/v1/workspaces/{workspace_id}/notes"

    @router.get(api, response_model=list[NoteResponse])
    def list_notes(
        context: WorkspaceContext = Depends(require_workspace),
        q: str = "",
        note_type: NoteType | None = Query(default=None, alias="type"),
        pinned: bool | None = None,
        source_app: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            return service.list_notes(
                context, query=q, note_type=note_type, pinned=pinned,
                source_app=source_app, source_type=source_type, source_id=source_id,
                limit=limit, offset=offset,
            )
        except Exception as error:
            raise _translate(error) from error

    @router.post(api, response_model=NoteResponse, status_code=201)
    def create_note(request: Request, payload: NoteCreate, context: WorkspaceContext = Depends(require_workspace)):
        mutate(request)
        try:
            return service.create_note(context, payload)
        except Exception as error:
            raise _translate(error) from error

    @router.get(f"{api}/{{note_id}}", response_model=NoteResponse)
    def get_note(note_id: int, context: WorkspaceContext = Depends(require_workspace)):
        try:
            return service.get_note(context, note_id)
        except Exception as error:
            raise _translate(error) from error

    @router.put(f"{api}/{{note_id}}", response_model=NoteResponse)
    def update_note(request: Request, note_id: int, payload: NoteUpdate, context: WorkspaceContext = Depends(require_workspace)):
        mutate(request)
        try:
            return service.update_note(context, note_id, payload)
        except Exception as error:
            raise _translate(error) from error

    @router.delete(f"{api}/{{note_id}}", status_code=204)
    def delete_note(request: Request, note_id: int, context: WorkspaceContext = Depends(require_workspace)):
        mutate(request)
        try:
            service.delete_note(context, note_id)
        except Exception as error:
            raise _translate(error) from error
        return Response(status_code=204, headers={"Content-Length": "0"})

    @router.post(f"{api}/{{note_id}}/restore", response_model=NoteResponse)
    def restore_note(request: Request, note_id: int, context: WorkspaceContext = Depends(require_workspace)):
        mutate(request)
        try:
            return service.restore_note(context, note_id)
        except Exception as error:
            raise _translate(error) from error

    @router.get(f"{api}/{{note_id}}/links", response_model=list[NoteLinkResponse])
    def list_links(note_id: int, context: WorkspaceContext = Depends(require_workspace)):
        try:
            return service.list_links(context, note_id)
        except Exception as error:
            raise _translate(error) from error

    @router.post(f"{api}/{{note_id}}/links", response_model=NoteLinkResponse, status_code=201)
    def create_link(request: Request, note_id: int, payload: SourceReferenceInput, context: WorkspaceContext = Depends(require_workspace)):
        mutate(request)
        try:
            return service.create_link(context, note_id, payload)
        except Exception as error:
            raise _translate(error) from error

    @router.delete(f"{api}/{{note_id}}/links/{{link_id}}", status_code=204)
    def delete_link(request: Request, note_id: int, link_id: int, context: WorkspaceContext = Depends(require_workspace)):
        mutate(request)
        try:
            service.delete_link(context, note_id, link_id)
        except Exception as error:
            raise _translate(error) from error
        return Response(status_code=204, headers={"Content-Length": "0"})

    return router


def create_legacy_notes_router(
    *,
    service: NotesService,
    require_legacy_workspace: Callable[[Request], WorkspaceContext],
    sunset: str = "Mon, 03 Aug 2027 00:00:00 GMT",
    csrf: CsrfDependency | None = None,
) -> APIRouter:
    """Authenticated legacy adapter; API calls never redirect across methods."""
    router = APIRouter()

    def context(request: Request) -> WorkspaceContext:
        return require_legacy_workspace(request)

    def deprecated(response: Response, workspace_id: str, suffix: str = "") -> None:
        response.headers["Deprecation"] = "true"
        response.headers["Sunset"] = sunset
        response.headers["Link"] = (
            f'</api/v1/workspaces/{workspace_id}/notes{suffix}>; rel="successor-version"'
        )

    @router.get("/apps/notes", include_in_schema=False)
    @router.get("/apps/notes/", include_in_schema=False)
    def legacy_ui(request: Request):
        try:
            ctx = context(request)
        except APIError as error:
            if error.status_code != 401:
                raise
            target = request.url.path
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(
                f"/login?next={quote(target, safe='')}", status_code=307
            )
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/w/{ctx.workspace_id}/notes{query}", status_code=307)

    @router.get("/apps/notes/new", include_in_schema=False)
    def legacy_new(request: Request):
        try:
            ctx = context(request)
        except APIError as error:
            if error.status_code != 401:
                raise
            target = request.url.path
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(
                f"/login?next={quote(target, safe='')}", status_code=307
            )
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/w/{ctx.workspace_id}/notes/new{query}", status_code=307)

    @router.api_route("/api/notes", methods=["GET", "POST"], include_in_schema=False)
    def collection(
        request: Request,
        response: Response,
        payload: NoteCreate | None = None,
        q: str = "",
        note_type: NoteType | None = Query(default=None, alias="type"),
        pinned: bool | None = None,
        source_app: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        ctx = context(request)
        deprecated(response, ctx.workspace_id)
        try:
            if request.method == "GET":
                notes = service.list_notes(
                    ctx,
                    query=q,
                    note_type=note_type,
                    pinned=pinned,
                    source_app=source_app,
                    source_type=source_type,
                    source_id=source_id,
                    limit=limit,
                    offset=offset,
                )
                return [NoteResponse.model_validate(note) for note in notes]
            if csrf is not None:
                csrf(request)
            assert payload is not None
            response.status_code = status.HTTP_201_CREATED
            return NoteResponse.model_validate(service.create_note(ctx, payload))
        except Exception as error:
            raise _translate(error) from error

    @router.api_route(
        "/api/notes/{note_id}/links", methods=["GET", "POST"], include_in_schema=False
    )
    def links(
        request: Request,
        response: Response,
        note_id: int,
        payload: SourceReferenceInput | None = None,
    ):
        ctx = context(request)
        deprecated(response, ctx.workspace_id, f"/{note_id}/links")
        try:
            if request.method == "GET":
                return service.list_links(ctx, note_id)
            if csrf is not None:
                csrf(request)
            assert payload is not None
            response.status_code = 201
            return service.create_link(ctx, note_id, payload)
        except Exception as error:
            raise _translate(error) from error

    @router.delete(
        "/api/notes/{note_id}/links/{link_id}", status_code=204,
        include_in_schema=False,
    )
    def unlink(request: Request, response: Response, note_id: int, link_id: int):
        ctx = context(request)
        deprecated(response, ctx.workspace_id, f"/{note_id}/links/{link_id}")
        if csrf is not None:
            csrf(request)
        try:
            service.delete_link(ctx, note_id, link_id)
        except Exception as error:
            raise _translate(error) from error
        response.headers["Content-Length"] = "0"
        return None

    @router.api_route(
        "/api/notes/{note_id}", methods=["GET", "PUT", "DELETE"],
        include_in_schema=False,
    )
    def item(request: Request, response: Response, note_id: int, payload: NoteUpdate | None = None):
        ctx = context(request)
        deprecated(response, ctx.workspace_id, f"/{note_id}")
        try:
            if request.method == "GET":
                return NoteResponse.model_validate(service.get_note(ctx, note_id))
            if csrf is not None:
                csrf(request)
            if request.method == "PUT":
                assert payload is not None
                return NoteResponse.model_validate(
                    service.update_note(ctx, note_id, payload)
                )
            service.delete_note(ctx, note_id)
            response.status_code = 204
            response.headers["Content-Length"] = "0"
            return None
        except Exception as error:
            raise _translate(error) from error

    return router


class _StandaloneAuthorization:
    """Compatibility-only policy for the unauthenticated standalone demo app."""
    _permissions = frozenset({
        "notes.read_all", "notes.create", "notes.update_all", "notes.delete_all", "notes.manage"
    })

    def require(self, context, permission_code=None, resource_owner_id=None, *, app_key=None, snapshot=None):
        if permission_code and permission_code not in self._permissions:
            raise PermissionDenied("permission denied")
        return PermissionSnapshot(
            context.workspace_id, context.membership_id, context.role_key,
            context.authorization_version, (("notes", "standalone-notes"),),
            self._permissions, frozenset(), frozenset(), frozenset(), frozenset({"notes"}),
        )

    def authorize(self, context, permission_code, resource_owner_id=None, *, snapshot=None):
        return permission_code in self._permissions


def create_app(
    database_path: str | Path | None = None,
    *,
    initialize_on_startup: bool = True,
    app_registry: AppRegistry | None = None,
    source_resolver: SourceReferenceResolver | None = None,
) -> FastAPI:
    """Backward-compatible standalone demo wrapper around the canonical service."""
    path = resolve_database_path(database_path)
    registry = app_registry or AppRegistry.load_default()
    authorization = _StandaloneAuthorization()
    context = WorkspaceContext(
        user_id="00000000-0000-4000-8000-000000000001",
        workspace_id="00000000-0000-4000-8000-000000000002",
        membership_id="00000000-0000-4000-8000-000000000003",
        role_key="owner", enabled_app_ids=("standalone-notes",), authorization_version=1,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if initialize_on_startup:
            if path.exists():
                verify_database(path, require_current=True)
            else:
                initialize_database(path)
        yield

    application = FastAPI(title="Workspace Notes", version="0.2.0", lifespan=lifespan)
    service = NotesService(
        path, authorization=authorization, source_resolver=source_resolver  # type: ignore[arg-type]
    )
    application.state.database_path = path
    application.state.notes_service = service
    application.state.workspace_context = context
    application.mount("/static/icons", StaticFiles(directory=PORTAL_STATIC_DIR / "icons"), name="workspace-icons")
    application.mount("/static/shared", StaticFiles(directory=SHARED_STATIC_DIR), name="shared-static")
    application.mount("/static/notes", StaticFiles(directory=STATIC_DIR), name="notes-static")
    application.include_router(
        create_notes_router(
            service=service, app_registry=registry,
            require_workspace=lambda workspace_id, request: context,
        )
    )
    # Preserve the historic standalone URLs; production uses the authenticated
    # legacy adapter above instead of this fixed local context.
    application.include_router(
        create_legacy_notes_router(service=service, require_legacy_workspace=lambda request: context)
    )
    @application.get("/", include_in_schema=False)
    def standalone_index():
        return RedirectResponse(f"/w/{context.workspace_id}/notes", 307)

    @application.get("/new", include_in_schema=False)
    def standalone_new():
        return RedirectResponse(f"/w/{context.workspace_id}/notes/new", 307)
    return application


app = create_app()


__all__ = ("STATIC_DIR", "TEMPLATES_DIR", "create_app", "create_legacy_notes_router", "create_notes_router")
