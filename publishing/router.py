from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from publishing import repository
from publishing.contracts import PublicationView, VersionView
from publishing.database import database_connection
from publishing.registry import PublicationTypeRegistry
from publishing.service import (
    DuplicateSlugError,
    InvalidPublicationTransitionError,
    InvalidSlugError,
    PublicationNotFoundError,
    PublicationTypeDisabledError,
    PublicationTypeNotFoundError,
    PublicationValidationError,
    PublicationVersionNotFoundError,
    PublishingPersistenceError,
    PublishingService,
    SourceNoteNotFoundError,
)
from publishing.search import SqliteLikePublicationSearchProvider
from publishing.source_notes import NoteSnapshotReader
from publishing.type_manifests import (
    DeclarativePublicationType,
    PublicationTypeDefinition,
    PublicationTypeManifestStore,
    declarative_route_prefix,
)
from publishing.types import default_publication_types
from shared.app_registry import AppRegistry
from shared.templating import create_templates, shell_context
from shared.document_export import (
    AssetResource,
    DocumentExportService,
    DocumentSnapshot,
    ExportConversionFailed,
    ExportResourceLimitExceeded,
    ExportTimeout,
    ExportUnavailable,
    attachment_headers,
)
from access.context import WorkspaceContext
from access.errors import AppNotAvailable, PermissionDenied
from access.service import AuthorizationService

PUBLISHING_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PUBLISHING_DIR / "templates"
STATIC_DIR = PUBLISHING_DIR / "static"
templates = create_templates(TEMPLATES_DIR)
logger = logging.getLogger(__name__)


class CreatePublicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: int = Field(gt=0)
    type_id: str = Field(min_length=1, max_length=40)
    slug: str = Field(min_length=1, max_length=80)
    summary: str = Field(default="", max_length=2000)


class SaveDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str | None = Field(default=None, max_length=2000)


class PreviewFromNoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note_id: int = Field(gt=0)
    type_id: str = Field(min_length=1, max_length=40)
    title: str | None = Field(default=None, max_length=300)
    summary: str = Field(default="", max_length=2000)


def _publication_type_payload(provider: Any) -> dict[str, Any]:
    options = provider.render_options()
    return {
        "type_id": provider.type_id,
        "display_name": provider.display_name,
        "icon": provider.icon,
        "order": provider.order,
        "route_prefix": provider.route_prefix,
        "render_options": {
            "toc_enabled": options.toc_enabled,
            "toc_levels": list(options.toc_levels),
            "related_enabled": options.related_enabled,
            "version_visible": options.version_visible,
            "backlink_enabled": options.backlink_enabled,
        },
    }


def _render_options(provider: Any) -> dict[str, Any]:
    options = provider.render_options()
    return {
        "showToc": options.toc_enabled,
        "tocLevels": list(options.toc_levels),
        "compact": False,
        "emptyMessage": "내용이 없습니다.",
    }


def _manifest_payload(
    definition: PublicationTypeDefinition,
    *,
    restart_required: bool,
) -> dict[str, Any]:
    return {
        **definition.model_dump(),
        "icon": "/static/icons/publication.svg",
        "route_prefix": declarative_route_prefix(definition.type_id),
        "render_options": {
            "toc_enabled": definition.toc_enabled,
            "toc_levels": [2, 3, 4],
            "related_enabled": definition.related_enabled,
            "version_visible": definition.version_visible,
            "backlink_enabled": False,
        },
        "builtin": False,
        "restart_required": restart_required,
    }


def _management_publication(
    database_path: Path,
    publication_id: int,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    with database_connection(database_path) as connection:
        publication = repository.get_publication(
            connection, publication_id, workspace_id
        )
        if publication is None:
            raise PublicationNotFoundError(publication_id)
        projection = repository.get_projection_version(connection, publication)
    return {**publication, "version": projection}


def _translate_error(error: Exception) -> HTTPException:
    if isinstance(
        error,
        (
            PublicationNotFoundError,
            PublicationVersionNotFoundError,
            SourceNoteNotFoundError,
        ),
    ):
        return HTTPException(status_code=404, detail="Resource not found")
    if isinstance(
        error,
        (
            DuplicateSlugError,
            InvalidPublicationTransitionError,
        ),
    ):
        return HTTPException(status_code=409, detail=str(error))
    if isinstance(error, PublicationValidationError):
        return HTTPException(
            status_code=422,
            detail={"message": "Publication validation failed", "issues": error.issues},
        )
    if isinstance(
        error,
        (
            InvalidSlugError,
            PublicationTypeDisabledError,
            PublicationTypeNotFoundError,
        ),
    ):
        return HTTPException(status_code=422, detail=str(error))
    if isinstance(error, PublishingPersistenceError):
        return HTTPException(status_code=500, detail="Publishing persistence failed")
    return HTTPException(status_code=500, detail="Publishing operation failed")


def create_publishing_router(
    *,
    service: PublishingService,
    registry: PublicationTypeRegistry,
    note_reader: NoteSnapshotReader,
    app_registry: AppRegistry,
    database_path: Path,
    search_provider: SqliteLikePublicationSearchProvider,
    publication_version_reconciler: Callable[[int, int], None] | None = None,
    type_manifest_store: PublicationTypeManifestStore,
    is_workspace_publicly_available: Callable[[str], bool] | None = None,
    include_management_api: bool = True,
    identity_database_path: Path | None = None,
    document_export_service: DocumentExportService | None = None,
    load_published_export_asset: Callable[[str, int], AssetResource | None]
    | None = None,
) -> APIRouter:
    router = APIRouter()

    def author_for(publication: dict[str, Any]) -> dict[str, str] | None:
        if identity_database_path is None or not publication.get("created_by"):
            return None
        with sqlite3.connect(identity_database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT display_name, email_normalized FROM users WHERE id=?", (publication["created_by"],)).fetchone()
        return dict(row) if row is not None else None

    def refresh_type_registry() -> None:
        registry.replace_providers(
            (*default_publication_types(), *type_manifest_store.providers())
        )

    def reconcile_version(publication: dict[str, Any]) -> None:
        version = publication.get("draft_version")
        if publication_version_reconciler is None or version is None:
            return
        try:
            publication_version_reconciler(publication["id"], version)
        except Exception:
            logger.exception(
                "Publication asset reference reconcile failed",
                extra={
                    "publication_id": publication["id"],
                    "version": version,
                },
            )

    @router.get("/api/publication-types")
    async def list_publication_types() -> list[dict[str, Any]]:
        # The manifest may be changed by another local Workspace process (for
        # example, a maintenance/import command). Keep the long-lived server
        # snapshot aligned before exposing type-dependent read surfaces.
        refresh_type_registry()
        return [
            _publication_type_payload(provider)
            for provider in registry.get_creation_types()
        ]

    @router.get("/api/publication-type-manifests")
    async def list_type_manifests() -> list[dict[str, Any]]:
        stored_custom = {
            definition.type_id: definition
            for definition in type_manifest_store.get_all()
        }
        builtin = [
            {
                **_publication_type_payload(provider),
                "builtin": True,
                "restart_required": False,
            }
            for provider in registry.get_all()
            if not isinstance(provider, DeclarativePublicationType)
        ]
        custom = [
            _manifest_payload(
                definition,
                restart_required=False,
            )
            for definition in stored_custom.values()
        ]
        return sorted(
            [*builtin, *custom],
            key=lambda item: (item["order"], item["type_id"]),
        )

    def validate_manifest_candidate(
        definition: PublicationTypeDefinition,
        *,
        replacing: bool,
    ) -> None:
        existing_custom = {
            item.type_id: item for item in type_manifest_store.get_all()
        }
        active = registry.get(definition.type_id)
        if active is not None and definition.type_id not in existing_custom:
            raise HTTPException(status_code=409, detail="Built-in type is protected")
        if replacing and definition.type_id not in existing_custom:
            raise HTTPException(status_code=404, detail="Custom type not found")
        if not replacing and (
            definition.type_id in existing_custom or active is not None
        ):
            raise HTTPException(status_code=409, detail="Type ID already exists")
        occupied_orders = {
            provider.order: provider.type_id
            for provider in registry.get_all()
            if provider.type_id not in existing_custom
        }
        occupied_orders.update(
            {
                item.order: item.type_id
                for item in existing_custom.values()
                if item.type_id != definition.type_id
            }
        )
        if definition.order in occupied_orders:
            raise HTTPException(status_code=409, detail="Type order already exists")

    @router.post(
        "/api/publication-type-manifests",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_type_manifest(
        payload: PublicationTypeDefinition,
    ) -> dict[str, Any]:
        validate_manifest_candidate(payload, replacing=False)
        type_manifest_store.save(payload, replace=False)
        refresh_type_registry()
        return _manifest_payload(payload, restart_required=False)

    @router.put("/api/publication-type-manifests/{type_id}")
    async def update_type_manifest(
        type_id: str,
        payload: PublicationTypeDefinition,
    ) -> dict[str, Any]:
        if payload.type_id != type_id:
            raise HTTPException(status_code=422, detail="type_id cannot be changed")
        validate_manifest_candidate(payload, replacing=True)
        type_manifest_store.save(payload, replace=True)
        refresh_type_registry()
        return _manifest_payload(payload, restart_required=False)

    @router.delete("/api/publication-type-manifests/{type_id}")
    async def delete_type_manifest(type_id: str) -> dict[str, Any]:
        if type_manifest_store.get(type_id) is None:
            if registry.exists(type_id):
                raise HTTPException(status_code=409, detail="Built-in type is protected")
            raise HTTPException(status_code=404, detail="Custom type not found")
        with database_connection(database_path) as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM publications WHERE type_id = ?",
                (type_id,),
            ).fetchone()[0]
        if count:
            raise HTTPException(
                status_code=409,
                detail="A type with publications cannot be deleted; disable it instead",
            )
        type_manifest_store.delete(type_id)
        refresh_type_registry()
        return {
            "type_id": type_id,
            "deleted": True,
            "restart_required": False,
        }

    @router.post("/api/publications/preview-from-note")
    async def preview_from_note(payload: PreviewFromNoteRequest) -> dict[str, Any]:
        provider = registry.get(payload.type_id)
        if provider is None or not provider.enabled:
            raise HTTPException(status_code=422, detail="Unsupported publication type")
        note = note_reader.get_snapshot(payload.note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Note not found")
        return {
            "note_id": note.note_id,
            "type_id": provider.type_id,
            "title": note.title,
            "summary": payload.summary,
            "markdown": note.markdown,
            "source_note_updated_at": note.updated_at,
            "render_options": _render_options(provider),
        }

    @router.get("/api/publications")
    async def list_publications(
        note_id: int | None = Query(default=None, gt=0),
        type_id: str | None = None,
        publication_status: Literal["draft", "published", "archived"] | None = Query(
            default=None, alias="status"
        ),
    ) -> list[dict[str, Any]]:
        with database_connection(database_path) as connection:
            return repository.list_publications(
                connection,
                note_id=note_id,
                type_id=type_id,
                status=publication_status,
            )

    @router.get("/api/publications/search")
    async def search_publications(
        q: str = "",
        type_id: str | None = Query(default=None, alias="type"),
        limit: int = Query(default=20, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        try:
            return search_provider.search(
                q,
                type_id=type_id,
                status_scope="public",
                limit=limit,
                offset=offset,
            )
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @router.get("/api/publications/{publication_id}")
    async def get_publication(publication_id: int) -> dict[str, Any]:
        try:
            return _management_publication(database_path, publication_id)
        except PublicationNotFoundError as error:
            raise _translate_error(error) from error

    @router.post(
        "/api/publications",
        status_code=status.HTTP_201_CREATED,
    )
    async def create_publication(payload: CreatePublicationRequest) -> dict[str, Any]:
        try:
            publication = service.create_draft_from_note(
                note_id=payload.note_id,
                type_id=payload.type_id,
                slug=payload.slug,
                summary=payload.summary,
            )
            reconcile_version(publication)
            return publication
        except Exception as error:
            if isinstance(error, (
                DuplicateSlugError,
                InvalidSlugError,
                PublicationTypeDisabledError,
                PublicationTypeNotFoundError,
                SourceNoteNotFoundError,
                PublishingPersistenceError,
            )):
                raise _translate_error(error) from error
            raise

    @router.post("/api/publications/{publication_id}/drafts/from-note")
    async def save_draft(
        publication_id: int,
        payload: SaveDraftRequest | None = None,
    ) -> dict[str, Any]:
        try:
            publication = service.save_draft_from_note(
                publication_id,
                summary=payload.summary if payload else None,
            )
            reconcile_version(publication)
            return publication
        except Exception as error:
            if isinstance(error, (
                InvalidPublicationTransitionError,
                PublicationNotFoundError,
                PublicationTypeDisabledError,
                PublicationTypeNotFoundError,
                SourceNoteNotFoundError,
                PublishingPersistenceError,
            )):
                raise _translate_error(error) from error
            raise

    @router.delete(
        "/api/publications/{publication_id}/draft",
        status_code=status.HTTP_200_OK,
    )
    async def discard_draft(publication_id: int) -> dict[str, Any]:
        try:
            return service.discard_draft(publication_id)
        except (PublicationNotFoundError, InvalidPublicationTransitionError) as error:
            raise _translate_error(error) from error

    @router.post("/api/publications/{publication_id}/publish")
    async def publish(publication_id: int) -> dict[str, Any]:
        try:
            return service.publish(publication_id)
        except Exception as error:
            if isinstance(error, (
                PublicationNotFoundError,
                PublicationTypeDisabledError,
                PublicationTypeNotFoundError,
                PublicationValidationError,
                InvalidPublicationTransitionError,
            )):
                raise _translate_error(error) from error
            raise

    @router.post("/api/publications/{publication_id}/archive")
    async def archive(publication_id: int) -> dict[str, Any]:
        try:
            return service.archive(publication_id)
        except (PublicationNotFoundError, InvalidPublicationTransitionError) as error:
            raise _translate_error(error) from error

    @router.post("/api/publications/{publication_id}/restore")
    async def restore(publication_id: int) -> dict[str, Any]:
        try:
            publication = service.restore(publication_id)
            reconcile_version(publication)
            return publication
        except (
            PublicationNotFoundError,
            InvalidPublicationTransitionError,
            PublishingPersistenceError,
        ) as error:
            raise _translate_error(error) from error

    @router.get("/api/publications/{publication_id}/versions")
    async def list_versions(publication_id: int) -> list[dict[str, Any]]:
        with database_connection(database_path) as connection:
            if repository.get_publication(connection, publication_id) is None:
                raise HTTPException(status_code=404, detail="Publication not found")
            return repository.list_publication_versions(connection, publication_id)

    @router.get("/api/publications/{publication_id}/versions/{version}")
    async def get_version(publication_id: int, version: int) -> dict[str, Any]:
        with database_connection(database_path) as connection:
            result = repository.get_publication_version(
                connection, publication_id, version
            )
        if result is None:
            raise HTTPException(status_code=404, detail="Publication version not found")
        return result

    @router.post(
        "/api/publications/{publication_id}/versions/{version}/restore-as-draft"
    )
    async def restore_version(publication_id: int, version: int) -> dict[str, Any]:
        try:
            publication = service.restore_version_as_draft(publication_id, version)
            reconcile_version(publication)
            return publication
        except (
            PublicationNotFoundError,
            PublicationVersionNotFoundError,
            PublicationTypeDisabledError,
            PublicationTypeNotFoundError,
            InvalidPublicationTransitionError,
            PublishingPersistenceError,
        ) as error:
            raise _translate_error(error) from error

    @router.get("/publications/", include_in_schema=False)
    async def publication_home(request: Request):
        refresh_type_registry()
        with database_connection(database_path) as connection:
            publications = repository.list_publications(
                connection, status="published"
            )
        if is_workspace_publicly_available is not None:
            publications = [
                publication
                for publication in publications
                if is_workspace_publicly_available(publication["workspace_id"])
            ]
        publication_cards = [
            {
                **publication,
                "type_display_name": registry.require(
                    publication["type_id"]
                ).display_name,
                "type_icon": registry.require(publication["type_id"]).icon,
                "href": f"/p/{publication['public_id']}/{publication['slug']}",
                "author": author_for(publication),
            }
            for publication in publications
        ]
        context = shell_context(
            request,
            app_registry,
            current_app_id="publishing",
            page_title="Publications",
        )
        context["current_app_name"] = "Publications"
        context["publications"] = publication_cards
        context["publication_count"] = len(publication_cards)
        context["publication_type_count"] = len(
            {publication["type_id"] for publication in publication_cards}
        )
        return templates.TemplateResponse(
            request,
            "publication_home.html",
            context,
        )

    @router.get("/publications/types/", include_in_schema=False)
    async def publication_type_management(request: Request):
        context = shell_context(
            request,
            app_registry,
            current_app_id="publishing",
            page_title="Publication Types",
        )
        context["current_app_name"] = "Publication Types"
        return templates.TemplateResponse(
            request,
            "publication_types.html",
            context,
        )

    def render_publication(
        request: Request,
        *,
        public_id: str | None = None,
        slug: str | None = None,
        route_path: str | None = None,
    ):
        try:
            content = service.get_public_content(
                public_id=public_id, route_path=route_path
            )
        except PublicationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Publication not found") from error
        publication = content.publication
        if slug is not None and publication["slug"] != slug:
            raise HTTPException(status_code=404, detail="Publication not found")
        if (
            is_workspace_publicly_available is not None
            and not is_workspace_publicly_available(publication["workspace_id"])
        ):
            raise HTTPException(status_code=404, detail="Publication not found")
        type_id = publication["type_id"]
        provider = registry.get(type_id)
        if provider is None:
            refresh_type_registry()
            provider = registry.get(type_id)
            if provider is None:
                raise HTTPException(status_code=404, detail="Publication not found")
        version = content.version
        provider_context = provider.build_view_context(
            PublicationView(
                publication_id=publication["id"],
                type_id=publication["type_id"],
                slug=publication["slug"],
                title=version["title"],
                summary=version["summary"],
                status=publication["status"],
            ),
            VersionView(
                version=version["version"],
                title=version["title"],
                summary=version["summary"],
                markdown=version["markdown"],
            ),
            {},
        )
        context = shell_context(
            request,
            app_registry,
            current_app_id="publishing",
            page_title=version["title"],
        )
        context["current_app_name"] = provider.display_name
        context.update(
            {
                "publication": publication,
                "publication_author": author_for(publication),
                "publication_version": version,
                "publication_type": provider,
                "render_options": _render_options(provider),
                "provider_context": provider_context,
            }
        )
        return templates.TemplateResponse(request, provider.template_name, context)

    @router.get("/manual/{slug}", include_in_schema=False)
    async def manual_view(request: Request, slug: str):
        return render_publication(request, route_path=f"/manual/{slug}")

    @router.get("/wiki/{slug}", include_in_schema=False)
    async def wiki_view(request: Request, slug: str):
        return render_publication(request, route_path=f"/wiki/{slug}")

    @router.get(
        "/publications/view/{type_id}/{slug}",
        include_in_schema=False,
    )
    async def declarative_view(request: Request, type_id: str, slug: str):
        provider = registry.get(type_id)
        if not isinstance(provider, DeclarativePublicationType):
            raise HTTPException(status_code=404, detail="Publication not found")
        return render_publication(
            request, route_path=f"/publications/view/{type_id}/{slug}"
        )

    @router.get("/p/{public_id}/{slug}", include_in_schema=False)
    async def canonical_publication_view(
        request: Request, public_id: str, slug: str
    ):
        return render_publication(request, public_id=public_id, slug=slug)

    @router.get("/api/publications/public/{public_id}/export")
    def export_publication(
        public_id: str,
        format: Literal["docx", "pdf"] = Query(...),
    ) -> Response:
        if document_export_service is None:
            raise HTTPException(status_code=503, detail="Document export is unavailable")
        try:
            content = service.get_public_content(public_id=public_id)
            publication = content.publication
            if (
                is_workspace_publicly_available is not None
                and not is_workspace_publicly_available(publication["workspace_id"])
            ):
                raise PublicationNotFoundError(public_id)
            version = content.version
            exported = document_export_service.export(
                DocumentSnapshot(
                    title=version["title"],
                    markdown=version["markdown"],
                    summary=version["summary"],
                    version=f"v{version['version']}",
                    date=publication.get("published_at") or "",
                ),
                format,
                asset_loader=(
                    (lambda asset_id: load_published_export_asset(public_id, asset_id))
                    if load_published_export_asset is not None
                    else None
                ),
            )
        except PublicationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Publication not found") from error
        except ExportResourceLimitExceeded as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except ExportUnavailable as error:
            raise HTTPException(status_code=503, detail="Document export is unavailable") from error
        except ExportTimeout as error:
            raise HTTPException(status_code=504, detail=str(error)) from error
        except ExportConversionFailed as error:
            raise HTTPException(status_code=500, detail="Document conversion failed") from error
        return Response(
            content=exported.content,
            media_type=exported.media_type,
            headers=attachment_headers(exported.filename, public=True),
        )

    if not include_management_api:
        router.routes[:] = [
            route
            for route in router.routes
            if (
                not getattr(route, "path", "").startswith("/api/")
                or getattr(route, "path", "").startswith("/api/publications/public/")
            )
        ]
    return router


def create_workspace_publishing_router(
    *,
    service: PublishingService,
    registry: PublicationTypeRegistry,
    database_path: Path,
    require_workspace: Callable[..., WorkspaceContext],
    csrf: Callable[[Request], None] | None = None,
    publication_version_reconciler: Callable[
        [WorkspaceContext, int, int], None
    ]
    | None = None,
    authorization: AuthorizationService | None = None,
    app_registry: AppRegistry | None = None,
    type_manifest_store: PublicationTypeManifestStore | None = None,
    identity_database_path: Path | None = None,
) -> APIRouter:
    """Authenticated, tenant-scoped Publishing management API."""
    router = APIRouter()
    api = "/api/v1/workspaces/{workspace_id}/publications"

    def author_for(publication: dict[str, Any]) -> dict[str, str] | None:
        if identity_database_path is None or not publication.get("created_by"):
            return None
        with sqlite3.connect(identity_database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT display_name, email_normalized FROM users WHERE id=?", (publication["created_by"],)).fetchone()
        return dict(row) if row is not None else None

    def mutate(request: Request) -> None:
        if csrf is not None:
            csrf(request)

    def require(
        context: WorkspaceContext,
        permission: str,
        resource_owner_id: str | None = None,
    ) -> None:
        if authorization is None:
            return
        try:
            authorization.require(
                context, permission, resource_owner_id, app_key="publishing"
            )
        except AppNotAvailable as error:
            raise HTTPException(status_code=404, detail="Resource not found") from error
        except PermissionDenied as error:
            raise HTTPException(status_code=403, detail="Action forbidden") from error

    if app_registry is not None:
        @router.get("/w/{workspace_id}/publications", include_in_schema=False)
        async def workspace_publication_home(
            request: Request,
            workspace_id: str,
            context: WorkspaceContext = Depends(require_workspace),
        ):
            require(context, "publishing.read_all")
            with database_connection(database_path) as connection:
                publications = repository.list_publications(
                    connection, workspace_id=context.workspace_id, status="published"
                )
            cards = [
                {
                    **publication,
                    "type_display_name": registry.require(publication["type_id"]).display_name,
                    "type_icon": registry.require(publication["type_id"]).icon,
                    "href": f"/p/{publication['public_id']}/{publication['slug']}",
                    "author": author_for(publication),
                }
                for publication in publications
            ]
            canonical_routes = {
                "/": f"/w/{workspace_id}/home",
                "/apps/notes/": f"/w/{workspace_id}/notes",
                "/apps/calendar/": f"/w/{workspace_id}/calendar",
                "/apps/publishing/": f"/w/{workspace_id}/publications",
                "/publications/": f"/w/{workspace_id}/publications",
                "/publications/types/": f"/w/{workspace_id}/publications/types",
            }
            page = shell_context(
                request,
                app_registry,
                current_app_id="publishing",
                page_title="Publications",
                path_for=lambda path: canonical_routes.get(path, path),
                workspace_id=workspace_id,
            )
            page["current_app_name"] = "Publications"
            page["publications"] = cards
            page["publication_count"] = len(cards)
            page["publication_type_count"] = len(
                {publication["type_id"] for publication in cards}
            )
            return templates.TemplateResponse(request, "publication_home.html", page)

        @router.get("/w/{workspace_id}/publications/types", include_in_schema=False)
        async def workspace_publication_types(
            request: Request,
            workspace_id: str,
            context: WorkspaceContext = Depends(require_workspace),
        ):
            require(context, "publishing.manage")
            canonical_routes = {
                "/": f"/w/{workspace_id}/home",
                "/apps/notes/": f"/w/{workspace_id}/notes",
                "/apps/calendar/": f"/w/{workspace_id}/calendar",
                "/apps/publishing/": f"/w/{workspace_id}/publications",
                "/publications/": f"/w/{workspace_id}/publications",
                "/publications/types/": f"/w/{workspace_id}/publications/types",
            }
            page = shell_context(
                request,
                app_registry,
                current_app_id="publishing",
                page_title="Publication Types",
                path_for=lambda path: canonical_routes.get(path, path),
                workspace_id=workspace_id,
            )
            page["current_app_name"] = "Publication Types"
            page["publication_type_api_endpoint"] = (
                f"/api/v1/workspaces/{workspace_id}/publications/type-manifests"
            )
            return templates.TemplateResponse(request, "publication_types.html", page)

    def require_owned(
        context: WorkspaceContext,
        publication_id: int,
        action: Literal["update", "delete"],
    ) -> None:
        if authorization is None:
            return
        with database_connection(database_path) as connection:
            publication = repository.get_publication(
                connection, publication_id, context.workspace_id
            )
        if publication is None:
            raise HTTPException(status_code=404, detail="Resource not found")
        snapshot = authorization.permission_snapshot(context)
        if not authorization.authorize_app(
            context, "publishing", snapshot=snapshot
        ):
            raise HTTPException(status_code=404, detail="Resource not found")
        if authorization.authorize(
            context, f"publishing.{action}_all", snapshot=snapshot
        ) or authorization.authorize(
            context,
            f"publishing.{action}_own",
            publication["created_by"],
            snapshot=snapshot,
        ):
            return
        raise HTTPException(status_code=403, detail="Action forbidden")

    def reconcile(context: WorkspaceContext, publication: dict[str, Any]) -> None:
        version = publication.get("draft_version") or publication.get("published_version")
        if publication_version_reconciler is not None and version is not None:
            publication_version_reconciler(context, publication["id"], version)

    @router.get(f"{api}/types")
    async def list_types(
        context: WorkspaceContext = Depends(require_workspace),
    ) -> list[dict[str, Any]]:
        require(context, "publishing.read_all")
        return [
            _publication_type_payload(provider)
            for provider in registry.get_creation_types()
        ]

    if type_manifest_store is not None:
        def refresh_type_registry() -> None:
            registry.replace_providers(
                (*default_publication_types(), *type_manifest_store.providers())
            )

        def validate_manifest_candidate(
            definition: PublicationTypeDefinition,
            *,
            replacing: bool,
        ) -> None:
            existing_custom = {
                item.type_id: item for item in type_manifest_store.get_all()
            }
            active = registry.get(definition.type_id)
            if active is not None and definition.type_id not in existing_custom:
                raise HTTPException(status_code=409, detail="Built-in type is protected")
            if replacing and definition.type_id not in existing_custom:
                raise HTTPException(status_code=404, detail="Custom type not found")
            if not replacing and (
                definition.type_id in existing_custom or active is not None
            ):
                raise HTTPException(status_code=409, detail="Type ID already exists")
            occupied_orders = {
                provider.order: provider.type_id
                for provider in registry.get_all()
                if provider.type_id not in existing_custom
            }
            occupied_orders.update(
                {
                    item.order: item.type_id
                    for item in existing_custom.values()
                    if item.type_id != definition.type_id
                }
            )
            if definition.order in occupied_orders:
                raise HTTPException(status_code=409, detail="Type order already exists")

        @router.get(f"{api}/type-manifests")
        async def list_workspace_type_manifests(
            context: WorkspaceContext = Depends(require_workspace),
        ) -> list[dict[str, Any]]:
            require(context, "publishing.manage")
            stored_custom = {
                definition.type_id: definition
                for definition in type_manifest_store.get_all()
            }
            builtin = [
                {
                    **_publication_type_payload(provider),
                    "builtin": True,
                    "restart_required": False,
                }
                for provider in registry.get_all()
                if not isinstance(provider, DeclarativePublicationType)
            ]
            custom = [
                _manifest_payload(definition, restart_required=False)
                for definition in stored_custom.values()
            ]
            return sorted(
                [*builtin, *custom],
                key=lambda item: (item["order"], item["type_id"]),
            )

        @router.post(f"{api}/type-manifests", status_code=status.HTTP_201_CREATED)
        async def create_workspace_type_manifest(
            payload: PublicationTypeDefinition,
            request: Request,
            context: WorkspaceContext = Depends(require_workspace),
        ) -> dict[str, Any]:
            require(context, "publishing.manage")
            mutate(request)
            validate_manifest_candidate(payload, replacing=False)
            type_manifest_store.save(payload, replace=False)
            refresh_type_registry()
            return _manifest_payload(payload, restart_required=False)

        @router.put(f"{api}/type-manifests/{{type_id}}")
        async def update_workspace_type_manifest(
            type_id: str,
            payload: PublicationTypeDefinition,
            request: Request,
            context: WorkspaceContext = Depends(require_workspace),
        ) -> dict[str, Any]:
            require(context, "publishing.manage")
            mutate(request)
            if payload.type_id != type_id:
                raise HTTPException(status_code=422, detail="type_id cannot be changed")
            validate_manifest_candidate(payload, replacing=True)
            type_manifest_store.save(payload, replace=True)
            refresh_type_registry()
            return _manifest_payload(payload, restart_required=False)

        @router.delete(f"{api}/type-manifests/{{type_id}}")
        async def delete_workspace_type_manifest(
            type_id: str,
            request: Request,
            context: WorkspaceContext = Depends(require_workspace),
        ) -> dict[str, Any]:
            require(context, "publishing.manage")
            mutate(request)
            if type_manifest_store.get(type_id) is None:
                if registry.exists(type_id):
                    raise HTTPException(status_code=409, detail="Built-in type is protected")
                raise HTTPException(status_code=404, detail="Custom type not found")
            with database_connection(database_path) as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM publications WHERE type_id=?",
                    (type_id,),
                ).fetchone()[0]
            if count:
                raise HTTPException(
                    status_code=409,
                    detail="A type with publications cannot be deleted; disable it instead",
                )
            type_manifest_store.delete(type_id)
            refresh_type_registry()
            return {"type_id": type_id, "deleted": True, "restart_required": False}

    @router.post(f"{api}/preview-from-note")
    async def preview_from_note(
        payload: PreviewFromNoteRequest,
        context: WorkspaceContext = Depends(require_workspace),
    ) -> dict[str, Any]:
        require(context, "publishing.create")
        provider = registry.get(payload.type_id)
        if provider is None or not provider.enabled:
            raise HTTPException(status_code=422, detail="Unsupported publication type")
        note = service._reader(context).get_snapshot(payload.note_id)
        if note is None:
            raise HTTPException(status_code=404, detail="Note not found")
        return {
            "note_id": note.note_id,
            "type_id": provider.type_id,
            "title": note.title,
            "summary": payload.summary,
            "markdown": note.markdown,
            "source_note_updated_at": note.updated_at,
            "render_options": _render_options(provider),
        }

    @router.get(api)
    async def list_publications(
        note_id: int | None = Query(default=None, gt=0),
        type_id: str | None = None,
        publication_status: Literal["draft", "published", "archived"] | None = Query(default=None, alias="status"),
        context: WorkspaceContext = Depends(require_workspace),
    ) -> list[dict[str, Any]]:
        require(context, "publishing.read_all")
        with database_connection(database_path) as connection:
            return repository.list_publications(
                connection,
                workspace_id=context.workspace_id,
                note_id=note_id,
                type_id=type_id,
                status=publication_status,
            )

    @router.get(f"{api}/{{publication_id}}")
    async def get_publication(
        publication_id: int,
        context: WorkspaceContext = Depends(require_workspace),
    ) -> dict[str, Any]:
        require(context, "publishing.read_all")
        try:
            return _management_publication(
                database_path, publication_id, context.workspace_id
            )
        except PublicationNotFoundError as error:
            raise _translate_error(error) from error

    @router.post(api, status_code=status.HTTP_201_CREATED)
    async def create_publication(
        request: Request,
        payload: CreatePublicationRequest,
        context: WorkspaceContext = Depends(require_workspace),
    ) -> dict[str, Any]:
        require(context, "publishing.create")
        mutate(request)
        try:
            publication = service.create_draft_from_note(
                note_id=payload.note_id,
                type_id=payload.type_id,
                slug=payload.slug,
                summary=payload.summary,
                context=context,
            )
            reconcile(context, publication)
            return publication
        except Exception as error:
            if isinstance(error, (DuplicateSlugError, InvalidSlugError, PublicationTypeDisabledError, PublicationTypeNotFoundError, SourceNoteNotFoundError, PublishingPersistenceError)):
                raise _translate_error(error) from error
            raise

    @router.post(f"{api}/{{publication_id}}/drafts/from-note")
    async def save_draft(
        request: Request,
        publication_id: int,
        payload: SaveDraftRequest | None = None,
        context: WorkspaceContext = Depends(require_workspace),
    ) -> dict[str, Any]:
        require_owned(context, publication_id, "update")
        mutate(request)
        try:
            publication = service.save_draft_from_note(
                publication_id,
                summary=payload.summary if payload else None,
                context=context,
            )
            reconcile(context, publication)
            return publication
        except Exception as error:
            if isinstance(error, (InvalidPublicationTransitionError, PublicationNotFoundError, PublicationTypeDisabledError, PublicationTypeNotFoundError, SourceNoteNotFoundError, PublishingPersistenceError)):
                raise _translate_error(error) from error
            raise

    async def transition(
        request: Request,
        publication_id: int,
        action: str,
        context: WorkspaceContext,
    ) -> dict[str, Any]:
        require_owned(
            context,
            publication_id,
            "delete" if action in {"archive", "discard"} else "update",
        )
        mutate(request)
        operation = {
            "publish": service.publish,
            "archive": service.archive,
            "restore": service.restore,
            "discard": service.discard_draft,
        }[action]
        try:
            publication = operation(publication_id, context=context)
            if action in {"publish", "restore"}:
                reconcile(context, publication)
            return publication
        except (PublicationNotFoundError, PublicationValidationError, InvalidPublicationTransitionError, PublishingPersistenceError) as error:
            raise _translate_error(error) from error

    @router.post(f"{api}/{{publication_id}}/publish")
    async def publish(request: Request, publication_id: int, context: WorkspaceContext = Depends(require_workspace)):
        return await transition(request, publication_id, "publish", context)

    @router.post(f"{api}/{{publication_id}}/archive")
    async def archive(request: Request, publication_id: int, context: WorkspaceContext = Depends(require_workspace)):
        return await transition(request, publication_id, "archive", context)

    @router.post(f"{api}/{{publication_id}}/restore")
    async def restore(request: Request, publication_id: int, context: WorkspaceContext = Depends(require_workspace)):
        return await transition(request, publication_id, "restore", context)

    @router.delete(f"{api}/{{publication_id}}/draft")
    async def discard(request: Request, publication_id: int, context: WorkspaceContext = Depends(require_workspace)):
        return await transition(request, publication_id, "discard", context)

    @router.get(f"{api}/{{publication_id}}/versions")
    async def list_versions(publication_id: int, context: WorkspaceContext = Depends(require_workspace)):
        require(context, "publishing.read_all")
        with database_connection(database_path) as connection:
            if repository.get_publication(connection, publication_id, context.workspace_id) is None:
                raise HTTPException(status_code=404, detail="Publication not found")
            return repository.list_publication_versions(connection, publication_id)

    @router.get(f"{api}/{{publication_id}}/versions/{{version}}")
    async def get_version(publication_id: int, version: int, context: WorkspaceContext = Depends(require_workspace)):
        require(context, "publishing.read_all")
        with database_connection(database_path) as connection:
            if repository.get_publication(connection, publication_id, context.workspace_id) is None:
                raise HTTPException(status_code=404, detail="Publication not found")
            result = repository.get_publication_version(connection, publication_id, version)
        if result is None:
            raise HTTPException(status_code=404, detail="Publication version not found")
        return result

    @router.post(f"{api}/{{publication_id}}/versions/{{version}}/restore-as-draft")
    async def restore_version(request: Request, publication_id: int, version: int, context: WorkspaceContext = Depends(require_workspace)):
        require_owned(context, publication_id, "update")
        mutate(request)
        try:
            publication = service.restore_version_as_draft(
                publication_id, version, context=context
            )
            reconcile(context, publication)
            return publication
        except (PublicationNotFoundError, PublicationVersionNotFoundError, InvalidPublicationTransitionError, PublishingPersistenceError) as error:
            raise _translate_error(error) from error

    return router


__all__ = (
    "STATIC_DIR",
    "TEMPLATES_DIR",
    "create_publishing_router",
    "create_workspace_publishing_router",
)
