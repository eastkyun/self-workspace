from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from shared.internal_links import ProviderLink


class NotesInternalLinkProvider:
    type_id = "note"

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

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
            rows = connection.execute(
                f"SELECT id, title FROM notes WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
        return {
            str(note_id): ProviderLink(
                target=str(note_id),
                href=f"/apps/notes/?{urlencode({'note_id': note_id})}",
                label=title,
            )
            for note_id, title in rows
        }
