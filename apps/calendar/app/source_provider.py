from __future__ import annotations

import sqlite3
from pathlib import Path

from shared.source_references import ResolvedSource, SourceReference


class CalendarEventSourceProvider:
    def __init__(self, database_path: Path, workspace_id: str | None = None) -> None:
        self.database_path = database_path
        self.workspace_id = workspace_id

    def resolve(self, reference: SourceReference) -> ResolvedSource | None:
        if reference.source_app != "calendar" or reference.source_type != "event":
            raise ValueError("Calendar provider only supports calendar/event")
        if not reference.source_id.isdecimal() or int(reference.source_id) <= 0:
            raise ValueError("calendar event source_id must be a positive integer")
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
                row = connection.execute(
                    """SELECT title FROM tasks
                    WHERE workspace_id = ? AND id = ? AND deleted_at IS NULL""",
                    (self.workspace_id, int(reference.source_id)),
                ).fetchone()
            else:
                if self.workspace_id is not None:
                    raise RuntimeError(
                        "tenant-aware Calendar lookup requires schema v3"
                    )
                # Compatibility for the pre-cutover standalone Calendar v2 DB.
                row = connection.execute(
                    "SELECT title FROM tasks WHERE id = ?",
                    (int(reference.source_id),),
                ).fetchone()
        if row is None:
            return None
        return ResolvedSource(reference=reference, title=row[0])
