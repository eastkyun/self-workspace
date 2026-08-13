from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from identity.auth import (
    AuthenticationForbidden,
    AuthenticationRequired,
    AuthenticationService,
    LoginRateLimiter,
    SessionPolicy,
)
from identity.database import (
    initialize_database as initialize_identity_database,
    resolve_database_path as resolve_identity_database_path,
    verify_database as verify_identity_database,
)
from identity.notifications import LocalOutboxNotificationProvider
from identity.router import _auth_error, _require_same_origin, create_auth_router
from identity.workspace_router import create_workspace_router
from identity.workspace_shell import create_workspace_shell_router
from identity.workspaces import WorkspaceService
from access.context import WorkspaceContext
from access.dependencies import workspace_dependency
from access.errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from access.service import AuthorizationService
from workspace_platform.app_catalog import AppCatalogService
from workspace_platform.app_policy import AppPolicyNotFound, WorkspaceAppPolicyService
from workspace_platform.user_state import WorkspaceUserStateService
from workspace_platform.user_state_resources import WorkspaceUserStateResourceResolver
from workspace_platform.user_state_router import create_user_state_router
from workspace_platform.effective_apps import EffectiveWorkspaceApps
from workspace_platform.dashboard import DashboardService, DashboardWidgetRegistry
from workspace_platform.dashboard_router import create_dashboard_router
from workspace_platform.dashboard_providers import (
    ActivitySummaryWidget,
    FavoritesWidget,
    MyNotesWidget,
    RecentNotesWidget,
    RecentViewsWidget,
    SearchShortcutWidget,
    TodayCalendarWidget,
)
from workspace_platform.search import WorkspaceSearchService
from workspace_platform.search_providers import (
    CalendarWorkspaceSearchProvider,
    NotesWorkspaceSearchProvider,
    PublishingWorkspaceSearchProvider,
)
from workspace_platform.search_router import create_workspace_search_router

from assets.database import (
    database_connection as asset_database_connection,
    initialize_workspace_database as initialize_asset_database,
    resolve_database_path as resolve_asset_database_path,
    verify_workspace_database as verify_asset_database,
)
from assets import repository as asset_repository
from assets.router import (
    create_asset_router,
    create_public_asset_router,
    create_workspace_asset_router,
)
from assets.service import AssetService
from assets.sources import WorkspaceMarkdownSourceReader
from assets.storage import LocalFileStorage
from apps.calendar.app.main import (
    STATIC_DIR as CALENDAR_STATIC_DIR,
    create_calendar_router,
    create_legacy_calendar_router,
    initialize_workspace_database as initialize_database,
    resolve_database_path,
    verify_workspace_database as verify_database,
)
from apps.calendar.app.internal_link_provider import CalendarInternalLinkProvider
from apps.calendar.app.service import CalendarService
from apps.calendar.app.source_provider import CalendarEventSourceProvider
from apps.notes.app.database import (
    initialize_database as initialize_notes_database,
    resolve_database_path as resolve_notes_database_path,
    verify_database as verify_notes_database,
)
from apps.notes.app.main import (
    STATIC_DIR as NOTES_STATIC_DIR,
    create_app as create_notes_app,
    create_legacy_notes_router,
    create_notes_router,
)
from apps.notes.app.internal_link_provider import NotesInternalLinkProvider
from apps.notes.app.service import NotesService
from portal.quotes import (
    DEFAULT_QUOTES,
    Quote,
    QuoteSelector,
    choose_quote,
)
from publishing.database import (
    database_connection as publishing_database_connection,
    initialize_workspace_database as initialize_publishing_database,
    resolve_database_path as resolve_publishing_database_path,
    verify_workspace_database as verify_publishing_database,
)
from publishing.internal_link_provider import (
    create_publication_internal_link_providers,
)
from publishing.registry import create_default_registry
from publishing.router import STATIC_DIR as PUBLISHING_STATIC_DIR
from publishing.router import TEMPLATES_DIR as PUBLISHING_TEMPLATES_DIR
from publishing.router import (
    create_publishing_router,
    create_workspace_publishing_router,
)
from publishing.service import PublishingService
from publishing.search import SqliteLikePublicationSearchProvider
from publishing.source_notes import NotesServiceSnapshotReader
from publishing.type_manifests import (
    PublicationTypeManifestStore,
)
from shared.app_registry import AppManifest, AppRegistry
from shared.config import load_config
from shared.errors import APIError, install_exception_handlers
from shared.security_logging import install_access_log_redaction
from shared.activity import SqliteActivitySink
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
RUNTIME_APP_KEYS = frozenset(
    {"home", "notes", "calendar", "search", "publishing", "assets", "workspace"}
)


class _WorkspaceInternalRouteRegistry:
    """Allow links only inside the active Workspace's canonical app routes."""

    def __init__(self, workspace_id: str) -> None:
        self.prefixes = tuple(
            f"/w/{workspace_id}/{app_key}" for app_key in ("notes", "calendar")
        )

    def resolve_route(self, path: str) -> object | None:
        if any(path == prefix or path.startswith(f"{prefix}/") for prefix in self.prefixes):
            return object()
        return None


def create_app(
    database_path: str | Path | None = None,
    *,
    notes_database_path: str | Path | None = None,
    publishing_database_path: str | Path | None = None,
    asset_database_path: str | Path | None = None,
    asset_blob_root: str | Path | None = None,
    publication_types_path: str | Path | None = None,
    identity_database_path: str | Path | None = None,
    authentication_service: AuthenticationService | None = None,
    app_registry: AppRegistry | None = None,
    quotes: Sequence[Quote] | None = None,
    quote_selector: QuoteSelector | None = None,
) -> FastAPI:
    install_access_log_redaction()
    configuration = load_config()
    legacy_test_compatibility = any(
        value is not None
        for value in (
            database_path,
            notes_database_path,
            publishing_database_path,
            asset_database_path,
        )
    )
    resolved_database_path = resolve_database_path(
        database_path if database_path is not None else configuration.databases.calendar
    )
    resolved_notes_database_path = resolve_notes_database_path(
        notes_database_path
        if notes_database_path is not None
        else configuration.databases.notes
    )
    if identity_database_path is not None:
        resolved_identity_database_path = resolve_identity_database_path(
            identity_database_path
        )
    elif any(
        value is not None
        for value in (database_path, notes_database_path, publishing_database_path, asset_database_path)
    ):
        # Any explicitly injected domain path denotes an isolated application
        # bundle (not the operational defaults). Keep Identity in that same
        # temporary bundle so compatibility tests cannot mutate production data.
        anchor = (
            resolved_notes_database_path
            if notes_database_path is not None or database_path is not None
            else Path(publishing_database_path).expanduser().resolve()
            if publishing_database_path is not None
            else Path(asset_database_path).expanduser().resolve()
        )
        resolved_identity_database_path = (anchor.parent / "identity.db").resolve()
    else:
        resolved_identity_database_path = configuration.databases.identity
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
        resolved_publishing_database_path = configuration.databases.publishing
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
        resolved_asset_database_path = configuration.databases.assets
    resolved_asset_blob_root = (
        Path(asset_blob_root).expanduser().resolve()
        if asset_blob_root is not None
        else (resolved_asset_database_path.parent / "blobs").resolve()
    )
    registry = app_registry or AppRegistry.load_default()
    resolved_publication_types_path = publication_types_path
    if (
        resolved_publication_types_path is None
        and (
            database_path is not None
            or notes_database_path is not None
            or publishing_database_path is not None
        )
    ):
        resolved_publication_types_path = (
            resolved_publishing_database_path.parent / "publication-types.json"
        )
    elif resolved_publication_types_path is None:
        resolved_publication_types_path = configuration.databases.publication_types
    publication_type_manifest_store = PublicationTypeManifestStore(
        resolved_publication_types_path
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
        notes_application.state.notes_service,
        notes_application.state.workspace_context,
    )
    publishing_service = PublishingService(
        resolved_publishing_database_path,
        registry=publication_type_registry,
        note_reader=publication_note_reader,
        note_reader_factory=lambda context: NotesServiceSnapshotReader(
            workspace_notes_service, context
        ),
        default_context=notes_application.state.workspace_context,
    )
    publication_search_provider = SqliteLikePublicationSearchProvider(
        resolved_publishing_database_path
    )
    asset_markdown_source_reader = WorkspaceMarkdownSourceReader(
        notes_service=notes_application.state.notes_service,
        notes_context=notes_application.state.workspace_context,
        publishing_database_path=resolved_publishing_database_path,
    )
    asset_storage = LocalFileStorage(resolved_asset_blob_root)
    asset_service = AssetService(
        resolved_asset_database_path,
        storage=asset_storage,
        markdown_source_reader=asset_markdown_source_reader,
        markdown_source_reader_factory=lambda context: WorkspaceMarkdownSourceReader(
            notes_service=workspace_notes_service,
            notes_context=context,
            publishing_database_path=resolved_publishing_database_path,
        ),
        default_context=notes_application.state.workspace_context,
    )
    auth_service = authentication_service or AuthenticationService(
        resolved_identity_database_path,
        policy=SessionPolicy(
            idle_ttl=timedelta(seconds=configuration.session.idle_ttl_seconds),
            absolute_ttl=timedelta(seconds=configuration.session.absolute_ttl_seconds),
            max_active_per_user=configuration.session.max_active_per_user,
            last_seen_interval=timedelta(
                seconds=configuration.session.last_seen_interval_seconds
            ),
            reset_ttl=timedelta(
                seconds=configuration.security.password_reset_ttl_seconds
            ),
        ),
        rate_limiter=LoginRateLimiter(
            window=timedelta(seconds=configuration.security.login_window_seconds),
            max_failures=configuration.security.login_max_failures,
        ),
        notification_provider=LocalOutboxNotificationProvider(
            resolved_identity_database_path.parent / "outbox.jsonl"
        ),
        public_origin=configuration.public_origin or "http://localhost:8000",
    )
    workspace_service = WorkspaceService(
        resolved_identity_database_path,
        notification_provider=auth_service.notification_provider,
        public_origin=configuration.public_origin or "http://localhost:8000",
        clock=auth_service.clock,
    )
    authorization_service = AuthorizationService(resolved_identity_database_path)
    app_catalog_service = AppCatalogService(resolved_identity_database_path)
    workspace_app_policy = WorkspaceAppPolicyService(
        resolved_identity_database_path, authorization_service
    )
    activity_sink = SqliteActivitySink(resolved_identity_database_path)
    user_state_resources = WorkspaceUserStateResourceResolver(
        resolved_notes_database_path, resolved_database_path, authorization_service
    )
    workspace_user_state = WorkspaceUserStateService(
        resolved_identity_database_path, authorization_service, user_state_resources
    )
    effective_workspace_apps = EffectiveWorkspaceApps(
        resolved_identity_database_path, authorization_service
    )
    require_workspace = workspace_dependency(
        auth_service,
        authorization_service,
        configuration.session.cookie_name,
    )
    workspace_notes_service = NotesService(
        resolved_notes_database_path,
        authorization=authorization_service,
        activity_sink=activity_sink,
        source_resolver_factory=lambda context: SourceReferenceResolver(
            {
                ("calendar", "event"): CalendarEventSourceProvider(
                    resolved_database_path, context.workspace_id
                )
            }
        ),
    )
    workspace_calendar_service = CalendarService(
        resolved_database_path,
        authorization=authorization_service,
        activity_sink=activity_sink,
    )
    workspace_search_service = WorkspaceSearchService(
        authorization_service,
        (
            NotesWorkspaceSearchProvider(workspace_notes_service),
            CalendarWorkspaceSearchProvider(workspace_calendar_service),
            PublishingWorkspaceSearchProvider(str(resolved_publishing_database_path)),
        ),
    )
    dashboard_registry = DashboardWidgetRegistry(
        (
            FavoritesWidget(workspace_user_state),
            RecentViewsWidget(workspace_user_state),
            ActivitySummaryWidget(workspace_user_state),
            RecentNotesWidget(workspace_notes_service),
            MyNotesWidget(workspace_notes_service),
            TodayCalendarWidget(
                workspace_calendar_service,
                resolved_identity_database_path,
            ),
            SearchShortcutWidget(workspace_search_service),
        )
    )
    dashboard_service = DashboardService(
        resolved_identity_database_path,
        authorization_service,
        dashboard_registry,
    )

    def notes_csrf(request: Request) -> None:
        try:
            user = auth_service.authenticate(
                request.cookies.get(configuration.session.cookie_name)
            )
        except AuthenticationRequired as error:
            raise _auth_error(error) from error
        _require_same_origin(request, configuration.public_origin)
        try:
            auth_service.validate_csrf(
                user.session_id, request.headers.get("x-csrf-token")
            )
        except AuthenticationForbidden as error:
            raise _auth_error(error) from error

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

    def reconcile_workspace_publication_version(
        context: WorkspaceContext,
        publication_id: int,
        version: int,
    ) -> None:
        with publishing_database_connection(
            resolved_publishing_database_path
        ) as connection:
            row = connection.execute(
                """SELECT v.id FROM publication_versions AS v
                JOIN publications AS p ON p.id = v.publication_id
                WHERE v.publication_id = ? AND v.version = ?
                  AND p.workspace_id = ?""",
                (publication_id, version, context.workspace_id),
            ).fetchone()
        if row is not None:
            asset_ids = asset_service.reconcile(
                owner_type="publication_version",
                owner_id=str(row["id"]),
                field="markdown",
                context=context,
            )
            with asset_database_connection(resolved_asset_database_path) as assets:
                public_asset_ids = [
                    asset["public_id"]
                    for asset_id in asset_ids
                    if (
                        asset := asset_repository.get_asset(
                            assets, asset_id, context.workspace_id
                        )
                    )
                ]
            with publishing_database_connection(
                resolved_publishing_database_path
            ) as connection:
                connection.execute(
                    "DELETE FROM publication_public_assets WHERE publication_id = ? AND version = ?",
                    (publication_id, version),
                )
                connection.executemany(
                    """INSERT INTO publication_public_assets
                    (publication_id, version, workspace_id, asset_public_id)
                    VALUES (?, ?, ?, ?)""",
                    (
                        (publication_id, version, context.workspace_id, public_id)
                        for public_id in public_asset_ids
                    ),
                )

    def resolve_published_asset(
        publication_public_id: str, asset_id: int
    ) -> dict | None:
        with asset_database_connection(resolved_asset_database_path) as connection:
            asset = asset_repository.get_asset(connection, asset_id)
        if asset is None:
            return None
        if not is_workspace_publicly_available(asset["workspace_id"]):
            return None
        with publishing_database_connection(
            resolved_publishing_database_path
        ) as connection:
            allowed = connection.execute(
                """SELECT 1
                FROM publications AS p
                JOIN publication_public_assets AS pa
                  ON pa.publication_id = p.id
                 AND pa.version = p.published_version
                 AND pa.workspace_id = p.workspace_id
                WHERE p.public_id = ?
                  AND p.visibility = 'PUBLIC'
                  AND p.status = 'published'
                  AND p.workspace_id = ?
                  AND pa.asset_public_id = ?""",
                (
                    publication_public_id,
                    asset["workspace_id"],
                    asset["public_id"],
                ),
            ).fetchone()
        return asset if allowed is not None else None

    def is_workspace_publicly_available(workspace_id: str) -> bool:
        if (
            legacy_test_compatibility
            and workspace_id == notes_application.state.workspace_context.workspace_id
        ):
            return True
        from identity.database import database_connection as identity_connection

        with identity_connection(resolved_identity_database_path) as connection:
            return connection.execute(
                "SELECT 1 FROM workspaces WHERE id = ? AND status = 'ACTIVE'",
                (workspace_id,),
            ).fetchone() is not None
    # Notes and Calendar links are workspace-scoped and must not be resolved by
    # a global, ID-only provider.  Their request-scoped providers are wired by
    # the canonical workspace endpoint below.
    internal_link_resolver = InternalLinkResolverRegistry(
        {
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
        if resolved_identity_database_path.exists():
            initialize_identity_database(resolved_identity_database_path)
            verify_identity_database(resolved_identity_database_path)
        else:
            initialize_identity_database(resolved_identity_database_path)
            verify_identity_database(resolved_identity_database_path)
        app_catalog_service.sync(runtime_app_keys=RUNTIME_APP_KEYS)
        dashboard_registry.sync_catalog(resolved_identity_database_path)
        verify_identity_database(resolved_identity_database_path)
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
        title="Nest",
        version="1.0.0",
        lifespan=lifespan,
    )
    application.add_middleware(GZipMiddleware, minimum_size=1_000, compresslevel=6)
    install_exception_handlers(application)
    install_exception_handlers(notes_application)
    application.state.calendar_database_path = resolved_database_path
    application.state.identity_database_path = resolved_identity_database_path
    application.state.authentication_service = auth_service
    application.state.workspace_service = workspace_service
    application.state.notes_database_path = resolved_notes_database_path
    application.state.notes_service = workspace_notes_service
    application.state.calendar_service = workspace_calendar_service
    application.state.legacy_notes_service = notes_application.state.notes_service
    application.state.legacy_notes_context = notes_application.state.workspace_context
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
    application.state.authorization_service = authorization_service
    application.state.app_catalog_service = app_catalog_service
    application.state.workspace_app_policy = workspace_app_policy
    application.state.effective_workspace_apps = effective_workspace_apps
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
    application.mount(
        "/static/notes",
        StaticFiles(directory=NOTES_STATIC_DIR),
        name="notes-static",
    )
    application.mount(
        "/apps/notes/static/shared",
        StaticFiles(directory=SHARED_STATIC_DIR),
        name="legacy-notes-shared-static",
    )
    application.mount(
        "/apps/notes/static/icons",
        StaticFiles(directory=STATIC_DIR / "icons"),
        name="legacy-notes-icons",
    )
    application.mount(
        "/apps/notes/static",
        StaticFiles(directory=NOTES_STATIC_DIR),
        name="legacy-notes-static",
    )
    application.mount(
        "/apps/calendar/static/shared",
        StaticFiles(directory=SHARED_STATIC_DIR),
        name="legacy-calendar-shared-static",
    )
    application.mount(
        "/apps/calendar/static/icons",
        StaticFiles(directory=STATIC_DIR / "icons"),
        name="legacy-calendar-icons",
    )
    application.mount(
        "/apps/calendar/static",
        StaticFiles(directory=CALENDAR_STATIC_DIR),
        name="legacy-calendar-static",
    )
    application.mount("/static", StaticFiles(directory=STATIC_DIR), name="portal-static")
    application.include_router(create_auth_router(auth_service, configuration))
    application.include_router(
        create_workspace_router(
            workspace_service, auth_service, configuration, workspace_app_policy
        )
    )
    application.include_router(
        create_user_state_router(
            workspace_user_state, workspace_service, auth_service, configuration
        )
    )
    application.include_router(
        create_notes_router(
            service=workspace_notes_service,
            app_registry=registry,
            require_workspace=require_workspace,
            csrf=notes_csrf,
            csrf_cookie_name=f"{configuration.session.cookie_name}_csrf",
        )
    )

    def require_legacy_notes_workspace(request: Request) -> WorkspaceContext:
        try:
            user = auth_service.authenticate(
                request.cookies.get(configuration.session.cookie_name)
            )
        except AuthenticationRequired as error:
            raise _auth_error(error) from error
        workspaces = workspace_service.list_workspaces(user)
        selected = next(
            (
                item
                for item in workspaces
                if item["id"] == user.last_workspace_id
            ),
            workspaces[0] if workspaces else None,
        )
        if selected is None:
            raise APIError(
                status_code=404,
                code="workspace_not_found",
                message="Resource not found",
            )
        try:
            return authorization_service.resolve_context(user.id, selected["id"])
        except WorkspaceNotAvailable as error:
            raise APIError(
                status_code=404,
                code="workspace_not_found",
                message="Resource not found",
            ) from error

    @application.get("/apps/publishing/", include_in_schema=False)
    def legacy_publishing_app(request: Request):
        context = require_legacy_notes_workspace(request)
        try:
            workspace_app_policy.require_app(context, "publishing")
        except AppPolicyNotFound as error:
            raise APIError(
                status_code=404, code="app_not_found", message="Resource not found"
            ) from error
        return RedirectResponse(
            f"/w/{context.workspace_id}/publications", status_code=307
        )

    application.include_router(
        create_legacy_notes_router(
            service=workspace_notes_service,
            require_legacy_workspace=require_legacy_notes_workspace,
            csrf=notes_csrf,
        )
    )
    application.include_router(
        create_calendar_router(
            service=workspace_calendar_service,
            app_registry=registry,
            require_workspace=require_workspace,
            csrf=notes_csrf,
            csrf_cookie_name=f"{configuration.session.cookie_name}_csrf",
        )
    )
    application.include_router(
        create_legacy_calendar_router(
            service=workspace_calendar_service,
            require_legacy_workspace=require_legacy_notes_workspace,
            csrf=notes_csrf,
        )
    )
    application.include_router(
        create_workspace_publishing_router(
            service=publishing_service,
            registry=publication_type_registry,
            database_path=resolved_publishing_database_path,
            require_workspace=require_workspace,
            csrf=notes_csrf,
            publication_version_reconciler=reconcile_workspace_publication_version,
            authorization=authorization_service,
            app_registry=registry,
            type_manifest_store=publication_type_manifest_store,
            identity_database_path=resolved_identity_database_path,
        )
    )
    application.include_router(
        create_workspace_shell_router(
            workspace_service,
            auth_service,
            configuration,
            workspace_app_policy,
            effective_workspace_apps,
            workspace_user_state,
            dashboard_service,
        )
    )
    application.include_router(
        create_workspace_search_router(
            workspace_search_service,
            authorization_service,
            require_workspace,
        )
    )
    application.include_router(
        create_dashboard_router(
            dashboard_service,
            authorization_service,
            require_workspace,
            notes_csrf,
        )
    )
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
            is_workspace_publicly_available=is_workspace_publicly_available,
            include_management_api=legacy_test_compatibility,
            identity_database_path=resolved_identity_database_path,
        )
    )
    application.include_router(
        create_public_asset_router(
            storage=asset_storage,
            resolve_published_asset=resolve_published_asset,
        )
    )
    # Compatibility API for standalone/local integrations. Workspace UI uses
    # only the authenticated canonical router below.
    application.include_router(
        create_asset_router(
            service=asset_service,
            storage=asset_storage,
            require_legacy_workspace=(
                None if legacy_test_compatibility else require_legacy_notes_workspace
            ),
            csrf=None if legacy_test_compatibility else notes_csrf,
            authorization=None if legacy_test_compatibility else authorization_service,
        )
    )
    application.include_router(
        create_workspace_asset_router(
            service=asset_service,
            storage=asset_storage,
            require_workspace=require_workspace,
            csrf=notes_csrf,
            authorization=authorization_service,
        )
    )

    def render_hub(request: Request):
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
        return templates.TemplateResponse(request, "home.html", context)

    @application.get("/", include_in_schema=False)
    async def index(request: Request):
        try:
            authenticated = auth_service.authenticate(
                request.cookies.get(configuration.session.cookie_name)
            )
        except AuthenticationRequired:
            return RedirectResponse("/login", status_code=303)
        else:
            workspace_id = authenticated.last_workspace_id
            if workspace_id:
                return RedirectResponse(f"/w/{workspace_id}/home", status_code=303)
            return RedirectResponse("/workspace-selector", status_code=303)

    @application.get("/hub", include_in_schema=False)
    async def hub(request: Request):
        return render_hub(request)

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
        if not legacy_test_compatibility:
            raise APIError(
                status_code=404,
                code="legacy_endpoint_unavailable",
                message="Resource not found",
            )
        return internal_link_resolver.resolve_batch(payload.links)

    @application.post(
        "/api/v1/workspaces/{workspace_id}/internal-links/resolve",
        response_model=list[InternalLinkResult],
    )
    async def resolve_workspace_internal_links(
        payload: InternalLinkBatchRequest,
        context: WorkspaceContext = Depends(require_workspace),
    ) -> tuple[InternalLinkResult, ...]:
        providers = {}
        for app_key, permission_code, provider in (
            (
                "notes",
                "notes.read_all",
                NotesInternalLinkProvider(
                    resolved_notes_database_path, context.workspace_id
                ),
            ),
            (
                "calendar",
                "calendar.read_all",
                CalendarInternalLinkProvider(
                    resolved_database_path, context.workspace_id
                ),
            ),
        ):
            try:
                authorization_service.require(
                    context, permission_code, app_key=app_key
                )
            except (AppNotAvailable, PermissionDenied, WorkspaceNotAvailable):
                continue
            providers[provider.type_id] = provider
        try:
            authorization_service.require(
                context, "publishing.read_all", app_key="publishing"
            )
        except (AppNotAvailable, PermissionDenied, WorkspaceNotAvailable):
            pass
        else:
            providers.update(
                create_publication_internal_link_providers(
                    resolved_publishing_database_path,
                    publication_type_registry,
                    context.workspace_id,
                )
            )
        resolver = InternalLinkResolverRegistry(
            providers,
            registry,
            additional_route_registries=(
                _WorkspaceInternalRouteRegistry(context.workspace_id),
                publication_type_registry,
            ),
        )
        return resolver.resolve_batch(payload.links)

    return application


app = create_app()
