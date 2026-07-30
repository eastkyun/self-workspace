from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
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
)
from shared.app_registry import AppRegistry
from shared.templating import create_templates, shell_context

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
        "route_prefix": f"/{definition.type_id}",
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


def _provider_matches_definition(
    provider: Any,
    definition: PublicationTypeDefinition,
) -> bool:
    options = provider.render_options()
    return (
        provider.type_id == definition.type_id
        and provider.display_name == definition.display_name
        and provider.order == definition.order
        and provider.enabled is definition.enabled
        and options.toc_enabled is definition.toc_enabled
        and options.related_enabled is definition.related_enabled
        and options.version_visible is definition.version_visible
    )


def _management_publication(
    database_path: Path, publication_id: int
) -> dict[str, Any]:
    with database_connection(database_path) as connection:
        publication = repository.get_publication(connection, publication_id)
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
) -> APIRouter:
    router = APIRouter()

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
        active_custom_ids = {
            provider.type_id
            for provider in registry.get_all()
            if provider.type_id in stored_custom
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
                restart_required=definition.type_id not in active_custom_ids
                or not _provider_matches_definition(
                    registry.require(definition.type_id),
                    definition,
                ),
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
        return _manifest_payload(payload, restart_required=True)

    @router.put("/api/publication-type-manifests/{type_id}")
    async def update_type_manifest(
        type_id: str,
        payload: PublicationTypeDefinition,
    ) -> dict[str, Any]:
        if payload.type_id != type_id:
            raise HTTPException(status_code=422, detail="type_id cannot be changed")
        validate_manifest_candidate(payload, replacing=True)
        type_manifest_store.save(payload, replace=True)
        return _manifest_payload(payload, restart_required=True)

    @router.delete(
        "/api/publication-type-manifests/{type_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_type_manifest(type_id: str):
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
        return None

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
        with database_connection(database_path) as connection:
            publications = repository.list_publications(
                connection, status="published"
            )
        publication_cards = [
            {
                **publication,
                "href": (
                    f"{registry.require(publication['type_id']).route_prefix}/"
                    f"{publication['slug']}"
                ),
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

    def render_publication(request: Request, *, type_id: str, slug: str):
        provider = registry.get(type_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="Publication not found")
        try:
            content = service.get_public_content(type_id=type_id, slug=slug)
        except PublicationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Publication not found") from error
        publication = content.publication
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
                "publication_version": version,
                "publication_type": provider,
                "render_options": _render_options(provider),
                "provider_context": provider_context,
            }
        )
        return templates.TemplateResponse(request, provider.template_name, context)

    @router.get("/manual/{slug}", include_in_schema=False)
    async def manual_view(request: Request, slug: str):
        return render_publication(request, type_id="manual", slug=slug)

    @router.get("/wiki/{slug}", include_in_schema=False)
    async def wiki_view(request: Request, slug: str):
        return render_publication(request, type_id="wiki", slug=slug)

    def create_custom_view(type_id: str):
        async def custom_view(request: Request, slug: str):
            return render_publication(request, type_id=type_id, slug=slug)

        return custom_view

    for custom_provider in registry.get_all():
        if custom_provider.type_id in {"manual", "wiki"}:
            continue

        router.add_api_route(
            f"{custom_provider.route_prefix}/{{slug}}",
            create_custom_view(custom_provider.type_id),
            methods=["GET"],
            include_in_schema=False,
            name=f"publication-{custom_provider.type_id}",
        )

    return router


__all__ = ("STATIC_DIR", "TEMPLATES_DIR", "create_publishing_router")
