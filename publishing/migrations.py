"""Offline Publishing v1 -> v2 workspace migration (staging copies only)."""

from __future__ import annotations

from uuid import UUID

from shared.migrations import MigrationStep, get_user_version
from shared.migrations.domain_context import assert_backfilled, columns, index_columns, required_uuid, restore_sequence, sequence_value


def _public_id(context, publication_id: int) -> str:
    configured = context.get("publication_public_ids")
    if not isinstance(configured, dict) or str(publication_id) not in configured:
        raise ValueError("complete publication public ID mapping is required")
    value = configured[str(publication_id)]
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("publication public ID mapping contains an invalid UUID")
    return value


def _apply(connection, context) -> None:
    workspace_id = required_uuid(context, "workspace_id")
    actor_id = required_uuid(context, "actor_id")
    required_uuid(context, "release_id")
    sequences = {table: sequence_value(connection, table) for table in ("publications", "publication_versions")}
    connection.execute("ALTER TABLE publications ADD COLUMN workspace_id TEXT")
    connection.execute("ALTER TABLE publications ADD COLUMN created_by TEXT")
    connection.execute("ALTER TABLE publications ADD COLUMN visibility TEXT")
    connection.execute("ALTER TABLE publications ADD COLUMN public_id TEXT")
    rows = connection.execute("SELECT id FROM publications ORDER BY id").fetchall()
    connection.executemany(
        "UPDATE publications SET workspace_id=?, created_by=?, visibility='PUBLIC', public_id=? WHERE id=?",
        ((workspace_id, actor_id, _public_id(context, int(row[0])), int(row[0])) for row in rows),
    )
    # Replace global tenant-local slug uniqueness with workspace scope.
    connection.execute("ALTER TABLE publication_versions RENAME TO publication_versions_v1")
    connection.execute("ALTER TABLE publications RENAME TO publications_v1")
    connection.execute(
        """CREATE TABLE publications (
        id INTEGER PRIMARY KEY AUTOINCREMENT, note_id INTEGER NOT NULL, type_id TEXT NOT NULL,
        slug TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL,
        latest_version INTEGER NOT NULL, published_version INTEGER, draft_version INTEGER,
        published_at TEXT, archived_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        workspace_id TEXT NOT NULL, created_by TEXT NOT NULL,
        visibility TEXT NOT NULL CHECK(visibility IN ('INTERNAL','PUBLIC')),
        public_id TEXT UNIQUE,
        CHECK(status IN ('draft','published','archived')), CHECK(latest_version >= 1),
        CHECK(published_version IS NULL OR published_version BETWEEN 1 AND latest_version),
        CHECK(draft_version IS NULL OR draft_version BETWEEN 1 AND latest_version),
        CHECK(published_version IS NULL OR draft_version IS NULL OR draft_version > published_version),
        CHECK(status != 'draft' OR (published_version IS NULL AND draft_version IS NOT NULL)),
        CHECK(status != 'published' OR published_version IS NOT NULL),
        UNIQUE(workspace_id, type_id, slug))"""
    )
    connection.execute("INSERT INTO publications SELECT * FROM publications_v1")
    connection.execute(
        """CREATE TABLE publication_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, publication_id INTEGER NOT NULL, version INTEGER NOT NULL,
        title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', markdown TEXT NOT NULL,
        source_note_updated_at TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(publication_id) REFERENCES publications(id) ON DELETE CASCADE,
        CHECK(version >= 1), UNIQUE(publication_id,version))"""
    )
    connection.execute("INSERT INTO publication_versions SELECT * FROM publication_versions_v1")
    connection.execute("DROP TABLE publication_versions_v1")
    connection.execute("DROP TABLE publications_v1")
    for table, value in sequences.items(): restore_sequence(connection, table, value)
    connection.execute(
        """CREATE TABLE publication_route_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, publication_id INTEGER NOT NULL,
        workspace_id TEXT NOT NULL, route_path TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
        FOREIGN KEY(publication_id) REFERENCES publications(id) ON DELETE CASCADE)"""
    )
    aliases = connection.execute("SELECT id,type_id,slug,created_at FROM publications ORDER BY id").fetchall()
    alias_rows = []
    for row in aliases:
        publication_id, type_id, slug, created_at = int(row[0]), str(row[1]), str(row[2]), row[3]
        alias_rows.append((publication_id, workspace_id, f"/publications/view/{type_id}/{slug}", created_at))
        if type_id in {"manual", "wiki"}:
            alias_rows.append((publication_id, workspace_id, f"/{type_id}/{slug}", created_at))
    connection.executemany(
        "INSERT INTO publication_route_aliases(publication_id,workspace_id,route_path,created_at) VALUES(?,?,?,?)",
        alias_rows,
    )
    connection.execute(
        """CREATE TABLE publication_public_assets (
        publication_id INTEGER NOT NULL, version INTEGER NOT NULL, workspace_id TEXT NOT NULL,
        asset_public_id TEXT NOT NULL, PRIMARY KEY(publication_id,version,asset_public_id),
        FOREIGN KEY(publication_id,version) REFERENCES publication_versions(publication_id,version) ON DELETE CASCADE)"""
    )
    projection = context.get("public_asset_projection", ())
    if not isinstance(projection, (tuple, list)):
        raise ValueError("migration context public_asset_projection must be a sequence")
    connection.executemany(
        "INSERT INTO publication_public_assets(publication_id,version,workspace_id,asset_public_id) VALUES(?,?,?,?)",
        ((int(item[0]), int(item[1]), workspace_id, str(item[2])) for item in projection),
    )
    connection.execute("CREATE INDEX idx_publications_workspace_status ON publications(workspace_id, visibility, status, updated_at DESC, id DESC)")
    connection.execute("CREATE INDEX idx_publications_note ON publications(note_id, type_id, updated_at DESC, id DESC)")
    connection.execute("CREATE INDEX idx_publications_status_updated ON publications(status, updated_at DESC, id DESC)")
    connection.execute("CREATE INDEX idx_publication_versions_publication ON publication_versions(publication_id, version DESC)")
    connection.execute("CREATE INDEX idx_publication_route_aliases_publication ON publication_route_aliases(publication_id, route_path)")
    connection.execute(
        """CREATE TRIGGER publication_public_id_immutable
        BEFORE UPDATE OF public_id ON publications
        WHEN OLD.public_id IS NOT NULL AND NEW.public_id IS NOT OLD.public_id
        BEGIN SELECT RAISE(ABORT, 'publication public_id is immutable'); END"""
    )


def _verify(connection, context) -> None:
    workspace_id = required_uuid(context, "workspace_id")
    actor_id = required_uuid(context, "actor_id")
    if not {"workspace_id", "created_by", "visibility", "public_id"}.issubset(columns(connection, "publications")):
        raise RuntimeError("Publishing v2 columns are incomplete")
    info = {str(row[1]): row for row in connection.execute("PRAGMA table_info(publications)")}
    if any(info[field][3] != 1 for field in ("workspace_id", "created_by", "visibility")) or info["public_id"][3] != 0:
        raise RuntimeError("Publishing v2 tenancy column nullability is invalid")
    assert_backfilled(connection, "publications", ("workspace_id", "created_by"), workspace_id, actor_id)
    if connection.execute("SELECT COUNT(*) FROM publications WHERE visibility != 'PUBLIC' OR public_id IS NULL").fetchone()[0]:
        raise RuntimeError("Publishing v2 public fields were not fully backfilled")
    if ("workspace_id", "type_id", "slug") not in index_columns(connection, "publications"):
        raise RuntimeError("Publishing v2 tenant slug uniqueness is missing")
    if ("public_id",) not in index_columns(connection, "publications"):
        raise RuntimeError("Publishing v2 public ID uniqueness is missing")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='publication_public_id_immutable'").fetchone() is None:
        raise RuntimeError("Publishing v2 public ID immutability trigger is missing")
    for table in ("publication_route_aliases", "publication_public_assets"):
        if not columns(connection, table):
            raise RuntimeError(f"Publishing v2 table {table} is missing")
    schema = "".join("".join(str(row[0]).lower().split()) for row in connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL"))
    if "check(visibilityin('internal','public'))" not in schema:
        raise RuntimeError("Publishing v2 visibility constraint is missing")
    aliases = connection.execute("SELECT type_id,slug FROM publications ORDER BY id").fetchall()
    for type_id, slug in aliases:
        paths = {str(r[0]) for r in connection.execute("SELECT route_path FROM publication_route_aliases WHERE publication_id=(SELECT id FROM publications WHERE type_id=? AND slug=? LIMIT 1)", (type_id, slug))}
        if f"/publications/view/{type_id}/{slug}" not in paths or (type_id in {"manual", "wiki"} and f"/{type_id}/{slug}" not in paths):
            raise RuntimeError("Publishing v2 legacy route aliases are incomplete")
    if get_user_version(connection) != 2:
        raise RuntimeError("Publishing migration did not reach v2")


MIGRATIONS = (MigrationStep(1, 2, _apply, _verify),)
TARGET_VERSION = 2
