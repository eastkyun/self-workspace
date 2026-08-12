from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from shared.internal_links import ProviderLink


class CalendarInternalLinkProvider:
    type_id = "calendar"

    def __init__(self, database_path: Path, workspace_id: str | None = None) -> None:
        self.database_path = database_path
        self.workspace_id = workspace_id

    def resolve_many(self, targets: tuple[str, ...]) -> dict[str, ProviderLink]:
        numeric_targets = tuple(
            target
            for target in targets
            if target.isdecimal() and int(target) > 0
        )
        if not numeric_targets:
            return {}
        ids = tuple(int(target) for target in numeric_targets)
        placeholders = ", ".join("?" for _ in ids)
        database_uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as connection:
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(tasks)")
            }
            if "workspace_id" in columns:
                if self.workspace_id is None:
                    raise RuntimeError(
                        "workspace_id is required for a tenant-aware Calendar database"
                    )
                rows = connection.execute(
                    f"""SELECT id, title FROM tasks
                    WHERE workspace_id = ? AND deleted_at IS NULL
                    AND id IN ({placeholders})""",
                    (self.workspace_id, *ids),
                ).fetchall()
            else:
                if self.workspace_id is not None:
                    raise RuntimeError(
                        "tenant-aware Calendar lookup requires schema v3"
                    )
                # Compatibility for the pre-cutover standalone Calendar v2 DB.
                rows = connection.execute(
                    f"SELECT id, title FROM tasks WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
        route = (
            f"/w/{self.workspace_id}/calendar"
            if self.workspace_id is not None
            else "/apps/calendar/"
        )
        return {
            str(event_id): ProviderLink(
                target=str(event_id),
                href=f"{route}?{urlencode({'event_id': event_id})}",
                label=title,
            )
            for event_id, title in rows
        }
