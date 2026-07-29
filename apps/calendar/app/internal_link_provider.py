from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from shared.internal_links import ProviderLink


class CalendarInternalLinkProvider:
    type_id = "calendar"

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

    def resolve_many(self, targets: tuple[str, ...]) -> dict[str, ProviderLink]:
        if not targets:
            return {}
        ids = tuple(int(target) for target in targets)
        placeholders = ", ".join("?" for _ in ids)
        database_uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(database_uri, uri=True) as connection:
            rows = connection.execute(
                f"SELECT id, title FROM tasks WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        return {
            str(event_id): ProviderLink(
                target=str(event_id),
                href=f"/apps/calendar/?{urlencode({'event_id': event_id})}",
                label=title,
            )
            for event_id, title in rows
        }

