from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import Sequence
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from assets.database import (
    initialize_database as initialize_asset_database,
    resolve_database_path as resolve_asset_database_path,
    verify_database as verify_asset_database,
)
from assets.router import create_asset_router
from assets.service import AssetService
from assets.sources import WorkspaceMarkdownSourceReader
from assets.storage import LocalFileStorage
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
from publishing.database import (
    database_connection as publishing_database_connection,
    initialize_database as initialize_publishing_database,
    resolve_database_path as resolve_publishing_database_path,
    verify_database as verify_publishing_database,
)
from publishing.internal_link_provider import (
    create_publication_internal_link_providers,
)
from publishing.registry import create_default_registry
from publishing.projection import build_portal_projection
from publishing.router import STATIC_DIR as PUBLISHING_STATIC_DIR
from publishing.router import TEMPLATES_DIR as PUBLISHING_TEMPLATES_DIR
from publishing.router import create_publishing_router
from publishing.service import PublishingService
from publishing.search import SqliteLikePublicationSearchProvider
from publishing.source_notes import NotesServiceSnapshotReader
from publishing.type_manifests import (
    PublicationTypeManifestStore,
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
    publishing_database_path: str | Path | None = None,
    asset_database_path: str | Path | None = None,
    asset_blob_root: str | Path | None = None,
    publication_types_path: str | Path | None = None,
    app_registry: AppRegistry | None = None,
    quotes: Sequence[Quote] | None = None,
    quote_selector: QuoteSelector | None = None,
) -> FastAPI:
    resolved_database_path = resolve_database_path(database_path)
    resolved_notes_database_path = resolve_notes_database_path(notes_database_path)
    if publishing_database_path is not None:
        resolved_publishing_database_path = resolve_publishing_database_path(
            publishing_database_path
        )
    elif notes_database_path is not None:
        resolved_publishing_database_path = (
            resolved_notes_database_path.parent / "publishing.db"
        ).resolve()
    elif database_path is not None:
        resolved_publishing_database_path = (
            resolved_database_path.parent / "publishing.db"
        ).resolve()
    else:
        resolved_publishing_database_path = resolve_publishing_database_path()
    if asset_database_path is not None:
        resolved_asset_database_path = resolve_asset_database_path(
            asset_database_path
        )
    elif notes_database_path is not None:
        resolved_asset_database_path = (
            resolved_notes_database_path.parent / "assets.db"
        ).resolve()
    elif publishing_database_path is not None:
        resolved_asset_database_path = (
            resolved_publishing_database_path.parent / "assets.db"
        ).resolve()
    elif database_path is not None:
        resolved_asset_database_path = (
            resolved_database_path.parent / "assets.db"
        ).resolve()
    else:
        resolved_asset_database_path = resolve_asset_database_path()
    resolved_asset_blob_root = (
        Path(asset_blob_root).expanduser().resolve()
        if asset_blob_root is not None
        else (resolved_asset_database_path.parent / "blobs").resolve()
    )
    registry = app_registry or AppRegistry.load_default()
    publication_type_manifest_store = PublicationTypeManifestStore(
        publication_types_path
    )
    quote_corpus = DEFAULT_QUOTES if quotes is None else tuple(quotes)
    source_resolver = SourceReferenceResolver(
        {
            ("calendar", "event"): CalendarEventSourceProvider(
                resolved_database_path
            )
        }
    )
    notes_application = create_notes_app(
        resolved_notes_database_path,
        initialize_on_startup=False,
        app_registry=registry,
        source_resolver=source_resolver,
    )
    publication_type_registry = create_default_registry(
        icon_root=STATIC_DIR,
        template_root=PUBLISHING_TEMPLATES_DIR,
        additional_providers=publication_type_manifest_store.providers(),
    )
    publication_note_reader = NotesServiceSnapshotReader(
        notes_application.state.notes_service
    )
    publishing_service = PublishingService(
        resolved_publishing_database_path,
        registry=publication_type_registry,
        note_reader=publication_note_reader,
    )
    publication_search_provider = SqliteLikePublicationSearchProvider(
        resolved_publishing_database_path
    )
    asset_markdown_source_reader = WorkspaceMarkdownSourceReader(
        notes_service=notes_application.state.notes_service,
        publishing_database_path=resolved_publishing_database_path,
    )
    asset_storage = LocalFileStorage(resolved_asset_blob_root)
    asset_service = AssetService(
        resolved_asset_database_path,
        storage=asset_storage,
        markdown_source_reader=asset_markdown_source_reader,
    )

    def reconcile_publication_version(
        publication_id: int,
        version: int,
    ) -> None:
        with publishing_database_connection(
            resolved_publishing_database_path
        ) as connection:
            row = connection.execute(
                "SELECT id FROM publication_versions "
                "WHERE publication_id = ? AND version = ?",
                (publication_id, version),
            ).fetchone()
        if row is not None:
            asset_service.reconcile(
                owner_type="publication_version",
                owner_id=str(row["id"]),
                field="markdown",
            )
    internal_link_resolver = InternalLinkResolverRegistry(
        {
            "note": NotesInternalLinkProvider(resolved_notes_database_path),
            "calendar": CalendarInternalLinkProvider(resolved_database_path),
            **create_publication_internal_link_providers(
                resolved_publishing_database_path,
                publication_type_registry,
            ),
        },
        registry,
        additional_route_registries=(publication_type_registry,),
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
        if resolved_publishing_database_path.exists():
            verify_publishing_database(resolved_publishing_database_path)
        else:
            initialize_publishing_database(resolved_publishing_database_path)
            verify_publishing_database(resolved_publishing_database_path)
        if resolved_asset_database_path.exists():
            verify_asset_database(resolved_asset_database_path)
        else:
            initialize_asset_database(resolved_asset_database_path)
            verify_asset_database(resolved_asset_database_path)
        with publishing_database_connection(
            resolved_publishing_database_path
        ) as connection:
            stored_type_ids = tuple(
                row["type_id"]
                for row in connection.execute(
                    "SELECT DISTINCT type_id FROM publications ORDER BY type_id"
                )
            )
        publication_type_registry.validate_stored_type_ids(stored_type_ids)
        yield

    application = FastAPI(
        title="My Workspace",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.state.calendar_database_path = resolved_database_path
    application.state.notes_database_path = resolved_notes_database_path
    application.state.notes_service = notes_application.state.notes_service
    application.state.publishing_database_path = resolved_publishing_database_path
    application.state.asset_database_path = resolved_asset_database_path
    application.state.asset_blob_root = resolved_asset_blob_root
    application.state.app_registry = registry
    application.state.quote_corpus = quote_corpus
    application.state.source_resolver = source_resolver
    application.state.internal_link_resolver = internal_link_resolver
    application.state.publication_type_registry = publication_type_registry
    application.state.publication_type_manifest_store = (
        publication_type_manifest_store
    )
    application.state.publishing_service = publishing_service
    application.state.publication_search_provider = publication_search_provider
    application.state.asset_markdown_source_reader = asset_markdown_source_reader
    application.state.asset_storage = asset_storage
    application.state.asset_service = asset_service
    application.mount(
        "/static/shared",
        StaticFiles(directory=SHARED_STATIC_DIR),
        name="shared-static",
    )
    application.mount(
        "/static/publishing",
        StaticFiles(directory=PUBLISHING_STATIC_DIR),
        name="publishing-static",
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="portal-static")
    application.include_router(
        create_publishing_router(
            service=publishing_service,
            registry=publication_type_registry,
            note_reader=publication_note_reader,
            app_registry=registry,
            database_path=resolved_publishing_database_path,
            search_provider=publication_search_provider,
            publication_version_reconciler=reconcile_publication_version,
            type_manifest_store=publication_type_manifest_store,
        )
    )
    application.include_router(
        create_asset_router(
            service=asset_service,
            storage=asset_storage,
        )
    )

    @application.get("/", include_in_schema=False)
    async def index(request: Request):
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
        context["publication_projection"] = build_portal_projection(
            resolved_publishing_database_path,
            publication_type_registry,
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
    async def resolve_internal_links(
        payload: InternalLinkBatchRequest,
    ) -> tuple[InternalLinkResult, ...]:
        return internal_link_resolver.resolve_batch(payload.links)

    calendar_application = create_calendar_app(
        resolved_database_path,
        initialize_on_startup=False,
        app_registry=registry,
    )
    application.mount("/apps/calendar", calendar_application, name="calendar")
    application.mount("/apps/notes", notes_application, name="notes")
    return application


app = create_app()
