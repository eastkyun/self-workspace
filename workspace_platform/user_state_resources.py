"""Server-authoritative resolution for favorite and recent resources."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import logging
import sqlite3
from pathlib import Path

from access.context import WorkspaceContext
from access.service import AuthorizationService
from apps.calendar.app.internal_link_provider import CalendarInternalLinkProvider
from apps.notes.app.internal_link_provider import NotesInternalLinkProvider
from shared.internal_links import ProviderLink


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResourceResolution:
    status: str
    app_key: str
    link: ProviderLink | None = None


class WorkspaceUserStateResourceResolver:
    _APP_BY_TYPE = {"note": "notes", "calendar": "calendar"}
    _PERMISSION_BY_TYPE = {"note": "notes.read_all", "calendar": "calendar.read_all"}

    def __init__(
        self, notes_database_path: str | Path, calendar_database_path: str | Path,
        authorization: AuthorizationService,
    ) -> None:
        self.notes_database_path = Path(notes_database_path)
        self.calendar_database_path = Path(calendar_database_path)
        self.authorization = authorization

    def resolve_many(
        self, context: WorkspaceContext, grouped: Mapping[str, tuple[str, ...]],
    ) -> dict[tuple[str, str], ResourceResolution]:
        results: dict[tuple[str, str], ResourceResolution] = {}
        snapshot = self.authorization.permission_snapshot(context)
        for resource_type, resource_ids in grouped.items():
            app_key = self._APP_BY_TYPE.get(resource_type)
            permission = self._PERMISSION_BY_TYPE.get(resource_type)
            if app_key is None or permission is None:
                for resource_id in resource_ids:
                    results[(resource_type, resource_id)] = ResourceResolution("missing", "")
                continue
            if not self.authorization.authorize_app(context, app_key, snapshot=snapshot) or not self.authorization.authorize(context, permission, snapshot=snapshot):
                for resource_id in resource_ids:
                    results[(resource_type, resource_id)] = ResourceResolution("hidden", app_key)
                continue
            provider = (
                NotesInternalLinkProvider(self.notes_database_path, context.workspace_id)
                if resource_type == "note"
                else CalendarInternalLinkProvider(self.calendar_database_path, context.workspace_id)
            )
            try:
                links = provider.resolve_many(resource_ids)
            except (OSError, RuntimeError, sqlite3.Error) as error:
                logger.warning(
                    "workspace_user_state_provider_failed",
                    extra={
                        "workspace_id": context.workspace_id,
                        "resource_type": resource_type,
                        "app_key": app_key,
                        "error_type": type(error).__name__,
                    },
                )
                for resource_id in resource_ids:
                    results[(resource_type, resource_id)] = ResourceResolution("unavailable", app_key)
                continue
            for resource_id in resource_ids:
                link = links.get(resource_id)
                results[(resource_type, resource_id)] = ResourceResolution(
                    "visible" if link else "missing", app_key, link
                )
        return results

    def resolve_one(
        self, context: WorkspaceContext, resource_type: str, resource_id: str,
    ) -> ResourceResolution:
        return self.resolve_many(context, {resource_type: (resource_id,)})[(resource_type, resource_id)]


__all__ = ["ResourceResolution", "WorkspaceUserStateResourceResolver"]
