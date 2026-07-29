from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.staticfiles import StaticFiles

from apps.notes.app.database import (
    initialize_database,
    resolve_database_path,
    verify_database,
)
from apps.notes.app.schemas import (
    NoteCreate,
    NoteResponse,
    NoteType,
    NoteUpdate,
)
from apps.notes.app.service import NoteNotFoundError, NotesService
from shared.app_registry import AppRegistry, PORTAL_STATIC_DIR
from shared.templating import SHARED_STATIC_DIR, create_templates, shell_context

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"
templates = create_templates(TEMPLATES_DIR)


def create_app(
    database_path: str | Path | None = None,
    *,
    initialize_on_startup: bool = True,
    app_registry: AppRegistry | None = None,
) -> FastAPI:
    resolved_database_path = resolve_database_path(database_path)
    registry = app_registry or AppRegistry.load_default()
    service = NotesService(resolved_database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if initialize_on_startup:
            if resolved_database_path.exists():
                verify_database(resolved_database_path)
            else:
                initialize_database(resolved_database_path)
        yield

    application = FastAPI(
        title="Workspace Notes",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.state.database_path = resolved_database_path
    application.state.app_registry = registry
    application.state.notes_service = service
    application.mount(
        "/static/icons",
        StaticFiles(directory=PORTAL_STATIC_DIR / "icons"),
        name="workspace-icons",
    )
    application.mount(
        "/static/shared",
        StaticFiles(directory=SHARED_STATIC_DIR),
        name="shared-static",
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="notes-static")

    def render_notes(request: Request, *, mode: str):
        return templates.TemplateResponse(
            request,
            "notes.html",
            shell_context(
                request,
                registry,
                current_app_id="notes",
                page_title="Notes",
            )
            | {"notes_mode": mode},
        )

    @application.get("/", include_in_schema=False)
    def index(request: Request):
        return render_notes(request, mode="browse")

    @application.get("/new", include_in_schema=False)
    def new_note(request: Request):
        return render_notes(request, mode="new")

    @application.get("/api/notes", response_model=list[NoteResponse])
    def list_notes(
        q: str = "",
        note_type: NoteType | None = Query(default=None, alias="type"),
        pinned: bool | None = None,
        source_app: str | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict]:
        try:
            return service.list_notes(
                query=q,
                note_type=note_type,
                pinned=pinned,
                source_app=source_app,
                source_type=source_type,
                source_id=source_id,
                limit=limit,
                offset=offset,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @application.post(
        "/api/notes",
        response_model=NoteResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def create_note(payload: NoteCreate) -> dict:
        return service.create_note(payload)

    @application.get("/api/notes/{note_id}", response_model=NoteResponse)
    def get_note(note_id: int) -> dict:
        try:
            return service.get_note(note_id)
        except NoteNotFoundError as error:
            raise HTTPException(status_code=404, detail="Note not found") from error

    @application.put("/api/notes/{note_id}", response_model=NoteResponse)
    def update_note(note_id: int, payload: NoteUpdate) -> dict:
        try:
            return service.update_note(note_id, payload)
        except NoteNotFoundError as error:
            raise HTTPException(status_code=404, detail="Note not found") from error

    @application.delete(
        "/api/notes/{note_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    def delete_note(note_id: int) -> Response:
        try:
            service.delete_note(note_id)
        except NoteNotFoundError as error:
            raise HTTPException(status_code=404, detail="Note not found") from error
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return application


app = create_app()
