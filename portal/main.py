from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.calendar.app.main import (
    create_app as create_calendar_app,
    initialize_database,
    resolve_database_path,
    verify_database,
)
from portal.registry import STATIC_DIR, HubApp, load_apps
from shared.paths import CALENDAR_DB_PATH


def create_app(database_path: str | Path | None = None) -> FastAPI:
    resolved_database_path = resolve_database_path(database_path or CALENDAR_DB_PATH)
    registered_apps = load_apps()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if resolved_database_path.exists():
            verify_database(resolved_database_path)
        else:
            initialize_database(resolved_database_path)
            verify_database(resolved_database_path)
        yield

    application = FastAPI(
        title="My Workspace",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.calendar_database_path = resolved_database_path
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="portal-static")

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @application.get("/api/hub/apps", response_model=list[HubApp])
    def list_apps() -> list[HubApp]:
        return registered_apps

    calendar_application = create_calendar_app(
        resolved_database_path,
        initialize_on_startup=False,
    )
    application.mount("/apps/calendar", calendar_application, name="calendar")
    return application


app = create_app()
