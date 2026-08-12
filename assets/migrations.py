"""Offline Assets v1 -> v2 workspace migration (staging copies only)."""

from __future__ import annotations

from uuid import UUID

from shared.migrations import MigrationStep, get_user_version
from shared.migrations.domain_context import assert_backfilled, columns, index_columns, required_uuid, restore_sequence, sequence_value


def _apply(connection, context) -> None:
    workspace_id = required_uuid(context, "workspace_id")
    actor_id = required_uuid(context, "actor_id")
    required_uuid(context, "release_id")
    sequences = {table: sequence_value(connection, table) for table in ("assets", "asset_references")}
    connection.execute("ALTER TABLE assets ADD COLUMN workspace_id TEXT")
    connection.execute("ALTER TABLE assets ADD COLUMN uploaded_by TEXT")
    connection.execute("ALTER TABLE assets ADD COLUMN public_id TEXT")
    rows = connection.execute("SELECT id FROM assets ORDER BY id").fetchall()
    configured = context.get("asset_public_ids")
    def public_id(asset_id: int) -> str:
        if not isinstance(configured, dict) or str(asset_id) not in configured:
            raise ValueError("complete asset public ID mapping is required")
        value = configured[str(asset_id)]
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("asset public ID mapping contains an invalid UUID")
        return value
    connection.executemany(
        "UPDATE assets SET workspace_id=?, uploaded_by=?, public_id=? WHERE id=?",
        ((workspace_id, actor_id, public_id(int(r[0])), int(r[0])) for r in rows),
    )
    connection.execute("ALTER TABLE asset_references ADD COLUMN workspace_id TEXT")
    connection.execute("UPDATE asset_references SET workspace_id=?", (workspace_id,))
    connection.execute("ALTER TABLE asset_references RENAME TO asset_references_v1")
    connection.execute("ALTER TABLE assets RENAME TO assets_v1")
    connection.execute(
        """CREATE TABLE assets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sha256 TEXT NOT NULL, media_type TEXT NOT NULL,
        byte_size INTEGER NOT NULL, storage_key TEXT NOT NULL UNIQUE, original_name TEXT NOT NULL,
        created_at TEXT NOT NULL, workspace_id TEXT NOT NULL, uploaded_by TEXT NOT NULL,
        public_id TEXT NOT NULL UNIQUE, CHECK(length(sha256)=64), CHECK(byte_size>0),
        CHECK(media_type IN ('image/png','image/jpeg','image/webp')), UNIQUE(workspace_id,sha256))"""
    )
    connection.execute("INSERT INTO assets SELECT * FROM assets_v1")
    connection.execute(
        """CREATE TABLE asset_references (
        id INTEGER PRIMARY KEY AUTOINCREMENT, asset_id INTEGER NOT NULL,
        owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, field TEXT NOT NULL,
        created_at TEXT NOT NULL, workspace_id TEXT NOT NULL,
        FOREIGN KEY(asset_id) REFERENCES assets(id) ON DELETE CASCADE,
        UNIQUE(asset_id,owner_type,owner_id,field))"""
    )
    connection.execute("INSERT INTO asset_references SELECT * FROM asset_references_v1")
    connection.execute("DROP TABLE asset_references_v1")
    connection.execute("DROP TABLE assets_v1")
    for table, value in sequences.items(): restore_sequence(connection, table, value)
    connection.execute("CREATE INDEX idx_assets_workspace_created ON assets(workspace_id, created_at DESC, id DESC)")
    connection.execute("CREATE INDEX idx_asset_references_workspace_owner ON asset_references(workspace_id, owner_type, owner_id, field, asset_id)")
    connection.execute("CREATE INDEX idx_asset_references_owner ON asset_references(owner_type, owner_id, field, asset_id)")
    connection.execute(
        """CREATE TRIGGER asset_public_id_immutable
        BEFORE UPDATE OF public_id ON assets
        WHEN NEW.public_id IS NOT OLD.public_id
        BEGIN SELECT RAISE(ABORT, 'asset public_id is immutable'); END"""
    )


def _verify(connection, context) -> None:
    workspace_id = required_uuid(context, "workspace_id")
    actor_id = required_uuid(context, "actor_id")
    if not {"workspace_id", "uploaded_by", "public_id"}.issubset(columns(connection, "assets")):
        raise RuntimeError("Assets v2 columns are incomplete")
    if "workspace_id" not in columns(connection, "asset_references"):
        raise RuntimeError("Assets v2 reference workspace column is missing")
    asset_info = {str(row[1]): row for row in connection.execute("PRAGMA table_info(assets)")}
    reference_info = {str(row[1]): row for row in connection.execute("PRAGMA table_info(asset_references)")}
    if any(asset_info[field][3] != 1 for field in ("workspace_id", "uploaded_by", "public_id")) or reference_info["workspace_id"][3] != 1:
        raise RuntimeError("Assets v2 tenancy columns must be NOT NULL")
    assert_backfilled(connection, "assets", ("workspace_id", "uploaded_by"), workspace_id, actor_id)
    assert_backfilled(connection, "asset_references", ("workspace_id",), workspace_id)
    if connection.execute("SELECT COUNT(*) FROM assets WHERE public_id IS NULL").fetchone()[0]:
        raise RuntimeError("Assets v2 public IDs were not fully backfilled")
    if ("workspace_id", "sha256") not in index_columns(connection, "assets"):
        raise RuntimeError("Assets v2 tenant SHA uniqueness is missing")
    if ("public_id",) not in index_columns(connection, "assets"):
        raise RuntimeError("Assets v2 public ID uniqueness is missing")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='asset_public_id_immutable'").fetchone() is None:
        raise RuntimeError("Assets v2 public ID immutability trigger is missing")
    foreign_keys = connection.execute("PRAGMA foreign_key_list(asset_references)").fetchall()
    if not any(row[2] == "assets" and row[3] == "asset_id" and row[4] == "id" and str(row[6]).upper() == "CASCADE" for row in foreign_keys):
        raise RuntimeError("Assets v2 reference foreign key is missing")
    if get_user_version(connection) != 2:
        raise RuntimeError("Assets migration did not reach v2")


MIGRATIONS = (MigrationStep(1, 2, _apply, _verify),)
TARGET_VERSION = 2
