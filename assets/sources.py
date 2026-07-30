from __future__ import annotations

from pathlib import Path

from apps.notes.app.service import NoteNotFoundError, NotesService
from publishing.database import database_connection as publishing_database_connection


def _positive_integer(value: str) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 and str(parsed) == value else None


class WorkspaceMarkdownSourceReader:
    """Read persisted Markdown through app-owned, same-process boundaries."""

    def __init__(
        self,
        *,
        notes_service: NotesService,
        publishing_database_path: str | Path,
    ) -> None:
        self.notes_service = notes_service
        self.publishing_database_path = Path(publishing_database_path).resolve()

    def get_markdown(
        self,
        owner_type: str,
        owner_id: str,
        field: str,
    ) -> str | None:
        source_id = _positive_integer(owner_id)
        if source_id is None:
            return None

        if owner_type == "note" and field == "content":
            try:
                note = self.notes_service.get_note(source_id)
            except NoteNotFoundError:
                return None
            return str(note["content"])

        if owner_type == "publication_version" and field == "markdown":
            with publishing_database_connection(
                self.publishing_database_path
            ) as connection:
                row = connection.execute(
                    "SELECT markdown FROM publication_versions WHERE id = ?",
                    (source_id,),
                ).fetchone()
            return str(row["markdown"]) if row is not None else None

        return None
