"""One projection for sidebar, launcher, home cards, and direct API gates."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from access.context import PermissionSnapshot, WorkspaceContext
from access.service import AuthorizationService
from identity.database import database_connection


@dataclass(frozen=True, slots=True)
class EffectiveApp:
    key: str
    app_id: str
    display_name: str
    description: str
    icon: str
    route: str
    sidebar_visible: bool
    launcher_visible: bool
    home_card_visible: bool
    display_order: int
    settings: tuple[tuple[str, object], ...]


class EffectiveWorkspaceApps:
    def __init__(self, database_path: str | Path, authorization: AuthorizationService) -> None:
        self.database_path = Path(database_path).resolve()
        self.authorization = authorization

    def list(
        self,
        context: WorkspaceContext,
        *,
        snapshot: PermissionSnapshot | None = None,
    ) -> tuple[EffectiveApp, ...]:
        view = snapshot or self.authorization.permission_snapshot(context)
        with database_connection(self.database_path) as tx:
            rows = tx.query(
                "SELECT app.id, app.key, app.name, app.description, app.icon, policy.display_order, "
                "policy.settings_json, extension.manifest_json FROM workspace_apps AS policy "
                "JOIN apps AS app ON app.id=policy.app_id "
                "JOIN app_manifests AS extension ON extension.app_id=app.id "
                "WHERE policy.workspace_id=? AND policy.enabled=1 AND app.status='ACTIVE' "
                "ORDER BY policy.display_order, app.key",
                (context.workspace_id,),
            )
        projected: list[EffectiveApp] = []
        for row in rows:
            if not self.authorization.authorize_app(context, row["key"], snapshot=view):
                continue
            manifest = json.loads(row["manifest_json"])
            platform = manifest["platform"]
            projected.append(
                EffectiveApp(
                    key=row["key"], app_id=row["id"], display_name=manifest["display_name"],
                    description=row["description"], icon=row["icon"], route=platform["routes"]["ui"],
                    sidebar_visible=platform["sidebar"]["visible"], launcher_visible=platform["launcher"],
                    home_card_visible=platform["home_card"], display_order=row["display_order"],
                    settings=tuple(sorted(json.loads(row["settings_json"]).items())),
                )
            )
        return tuple(projected)

    def sidebar(self, context: WorkspaceContext) -> tuple[EffectiveApp, ...]:
        return tuple(app for app in self.list(context) if app.sidebar_visible)

    def launcher(self, context: WorkspaceContext) -> tuple[EffectiveApp, ...]:
        return tuple(app for app in self.list(context) if app.launcher_visible)

    def home_cards(self, context: WorkspaceContext) -> tuple[EffectiveApp, ...]:
        return tuple(app for app in self.list(context) if app.home_card_visible)
