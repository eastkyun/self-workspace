from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import Sequence
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from apps.calendar.app.main import (
    create_app as create_calendar_app,
    initialize_database,
    resolve_database_path,
    verify_database,
)
from apps.calendar.app.internal_link_provider import CalendarInternalLinkProvider
from apps.calendar.app.source_provider import CalendarEventSourceProvider
from apps.notes.app.database import (
    initialize_database as initialize_notes_database,
    resolve_database_path as resolve_notes_database_path,
    verify_database as verify_notes_database,
)
from apps.notes.app.main import create_app as create_notes_app
from apps.notes.app.internal_link_provider import NotesInternalLinkProvider
from portal.quotes import (
    DEFAULT_QUOTES,
    Quote,
    QuoteSelector,
    choose_quote,
)
from shared.app_registry import AppManifest, AppRegistry
from shared.templating import (
    SHARED_STATIC_DIR,
    create_templates,
    shell_context,
)
from shared.source_references import SourceReferenceResolver
from shared.internal_links import (
    InternalLinkBatchRequest,
    InternalLinkResolverRegistry,
    InternalLinkResult,
)

PORTAL_DIR = Path(__file__).resolve().parent
STATIC_DIR = PORTAL_DIR / "static"
PORTAL_TEMPLATES_DIR = PORTAL_DIR / "templates"
templates = create_templates(PORTAL_TEMPLATES_DIR)


def create_app(
    database_path: str | Path | None = None,
    *,
    notes_database_path: str | Path | None = None,
    app_registry: AppRegistry | None = None,
    quotes: Sequence[Quote] | None = None,
    quote_selector: QuoteSelector | None = None,
) -> FastAPI:
    resolved_database_path = resolve_database_path(database_path)
    resolved_notes_database_path = resolve_notes_database_path(notes_database_path)
    registry = app_registry or AppRegistry.load_default()
    quote_corpus = DEFAULT_QUOTES if quotes is None else tuple(quotes)
    source_resolver = SourceReferenceResolver(
        {
            ("calendar", "event"): CalendarEventSourceProvider(
                resolved_database_path
            )
        }
    )
    internal_link_resolver = InternalLinkResolverRegistry(
        {
            "note": NotesInternalLinkProvider(resolved_notes_database_path),
            "calendar": CalendarInternalLinkProvider(resolved_database_path),
        },
        registry,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if resolved_database_path.exists():
            verify_database(resolved_database_path)
        else:
            initialize_database(resolved_database_path)
            verify_database(resolved_database_path)
        if resolved_notes_database_path.exists():
            verify_notes_database(resolved_notes_database_path)
        else:
            initialize_notes_database(resolved_notes_database_path)
            verify_notes_database(resolved_notes_database_path)
        yield

    application = FastAPI(
        title="My Workspace",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.calendar_database_path = resolved_database_path
    application.state.notes_database_path = resolved_notes_database_path
    application.state.app_registry = registry
    application.state.quote_corpus = quote_corpus
    application.state.source_resolver = source_resolver
    application.state.internal_link_resolver = internal_link_resolver
    application.mount(
        "/static/shared",
        StaticFiles(directory=SHARED_STATIC_DIR),
        name="shared-static",
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="portal-static")

    @application.get("/", include_in_schema=False)
    def index(request: Request):
        context = shell_context(
            request,
            registry,
            current_app_id="home",
            page_title="Home",
        )
        context["hub_quote"] = choose_quote(
            quote_corpus,
            selector=quote_selector,
        )
        return templates.TemplateResponse(
            request,
            "home.html",
            context,
        )

    @application.get("/api/hub/apps", response_model=list[AppManifest])
    def list_apps() -> tuple[AppManifest, ...]:
        return registry.get_enabled()

    @application.post(
        "/api/internal-links/resolve",
        response_model=list[InternalLinkResult],
    )
    def resolve_internal_links(
        payload: InternalLinkBatchRequest,
    ) -> tuple[InternalLinkResult, ...]:
        return internal_link_resolver.resolve_batch(payload.links)

    calendar_application = create_calendar_app(
        resolved_database_path,
        initialize_on_startup=False,
        app_registry=registry,
    )
    application.mount("/apps/calendar", calendar_application, name="calendar")
    notes_application = create_notes_app(
        resolved_notes_database_path,
        initialize_on_startup=False,
        app_registry=registry,
        source_resolver=source_resolver,
    )
    application.mount("/apps/notes", notes_application, name="notes")
    return application


app = create_app()
