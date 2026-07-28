from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from apps.calendar.app.main import (
    create_app as create_calendar_app,
    initialize_database,
    resolve_database_path,
    verify_database,
)
from shared.app_registry import AppManifest, AppRegistry
from shared.templating import (
    SHARED_STATIC_DIR,
    create_templates,
    shell_context,
)

PORTAL_DIR = Path(__file__).resolve().parent
STATIC_DIR = PORTAL_DIR / "static"
PORTAL_TEMPLATES_DIR = PORTAL_DIR / "templates"
templates = create_templates(PORTAL_TEMPLATES_DIR)


def create_app(
    database_path: str | Path | None = None,
    *,
    app_registry: AppRegistry | None = None,
) -> FastAPI:
    resolved_database_path = resolve_database_path(database_path)
    registry = app_registry or AppRegistry.load_default()

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
    application.state.app_registry = registry
    application.mount(
        "/static/shared",
        StaticFiles(directory=SHARED_STATIC_DIR),
        name="shared-static",
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="portal-static")

    @application.get("/", include_in_schema=False)
    def index(request: Request):
        return templates.TemplateResponse(
            request,
            "home.html",
            shell_context(
                request,
                registry,
                current_app_id="home",
                page_title="Home",
            ),
        )

    @application.get("/api/hub/apps", response_model=list[AppManifest])
    def list_apps() -> tuple[AppManifest, ...]:
        return registry.get_enabled()

    calendar_application = create_calendar_app(
        resolved_database_path,
        initialize_on_startup=False,
        app_registry=registry,
    )
    application.mount("/apps/calendar", calendar_application, name="calendar")
    return application


app = create_app()
