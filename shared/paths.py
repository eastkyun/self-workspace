from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
INSTANCE_DIR = WORKSPACE_ROOT / "instance"
CALENDAR_DB_PATH = INSTANCE_DIR / "calendar" / "tasks.db"
