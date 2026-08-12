from pathlib import Path

from apps.calendar.app.main import initialize_database as initialize_calendar
from apps.notes.app.database import initialize_legacy_database as initialize_notes
from assets.database import initialize_database as initialize_assets
from publishing.database import initialize_database as initialize_publishing


def initialize_legacy_workspace(root: Path) -> Path:
    """Create an isolated pre-tenancy source bundle for bootstrap tests."""

    initialize_calendar(root / "instance/calendar/tasks.db", create_backup=False)
    initialize_notes(root / "instance/notes/notes.db")
    initialize_publishing(root / "instance/publishing/publishing.db")
    initialize_assets(root / "instance/assets/assets.db")
    (root / "instance/publishing/types.json").write_text("[]\n", encoding="utf-8")
    (root / "instance/assets/blobs").mkdir(parents=True, exist_ok=True)
    return root
