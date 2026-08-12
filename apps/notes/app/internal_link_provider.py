from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlencode

from shared.internal_links import ProviderLink


class NotesInternalLinkProvider:
    type_id = "note"

    def __init__(self, database_path: Path, workspace_id: str) -> None:
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
            rows = connection.execute(
                f"""SELECT id, title FROM notes
                WHERE workspace_id = ? AND deleted_at IS NULL
                AND id IN ({placeholders})""",
                (self.workspace_id, *ids),
            ).fetchall()
        return {
            str(note_id): ProviderLink(
                target=str(note_id),
                href=(
                    f"/w/{self.workspace_id}/notes?"
                    f"{urlencode({'note_id': note_id})}"
                ),
                label=title,
            )
            for note_id, title in rows
        }
