from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from apps.calendar.app.main import initialize_database as initialize_calendar
from apps.calendar.migrations import MIGRATIONS as CALENDAR, TARGET_VERSION as CALENDAR_VERSION
from apps.notes.app.database import initialize_legacy_database as initialize_notes
from apps.notes.migrations import MIGRATIONS as NOTES, TARGET_VERSION as NOTES_VERSION
from assets.database import initialize_database as initialize_assets
from assets.migrations import MIGRATIONS as ASSETS, TARGET_VERSION as ASSETS_VERSION
from publishing.database import initialize_database as initialize_publishing
from publishing.migrations import MIGRATIONS as PUBLISHING, TARGET_VERSION as PUBLISHING_VERSION
from shared.migrations import migrate
from tools.staging_migrations import PRESERVED_COLUMNS, _snapshot


@pytest.fixture
def context():
    return {"workspace_id": str(uuid4()), "actor_id": str(uuid4()), "release_id": str(uuid4())}


def _seed_sources(root: Path) -> dict[str, Path]:
    paths = {name: root / f"{name}.db" for name in ("calendar", "notes", "publishing", "assets")}
    initialize_calendar(paths["calendar"], create_backup=False)
    initialize_notes(paths["notes"])
    initialize_publishing(paths["publishing"])
    initialize_assets(paths["assets"])
    with sqlite3.connect(paths["calendar"]) as c:
        c.execute("INSERT INTO tasks(title,description,status,position,all_day,start_at,due_at,created_at,updated_at) VALUES('task','body','todo',0,0,'2026-01-01T09:00:00+09:00','2026-01-01T10:00:00+09:00','c','u')")
        c.execute("INSERT INTO topics(name,color,created_at,updated_at) VALUES('Topic','#112233','c','u')")
        c.execute("INSERT INTO task_topics VALUES(1,1)")
    with sqlite3.connect(paths["notes"]) as c:
        c.execute("INSERT INTO notes(title,content,note_type,is_pinned,created_at,updated_at) VALUES('note','exact body','general',1,'c','u')")
        c.execute("INSERT INTO note_links(note_id,source_app,source_type,source_id,source_title_snapshot,created_at) VALUES(1,'calendar','event','1','task','c')")
    with sqlite3.connect(paths["publishing"]) as c:
        c.execute("INSERT INTO publications(note_id,type_id,slug,title,summary,status,latest_version,published_version,draft_version,published_at,archived_at,created_at,updated_at) VALUES(1,'manual','guide','Guide','sum','published',1,1,NULL,'p',NULL,'c','u')")
        c.execute("INSERT INTO publication_versions(publication_id,version,title,summary,markdown,source_note_updated_at,created_at) VALUES(1,1,'Guide','sum','# exact','u','c')")
    with sqlite3.connect(paths["assets"]) as c:
        c.execute("INSERT INTO assets(sha256,media_type,byte_size,storage_key,original_name,created_at) VALUES(?, 'image/png',3,'abc','a.png','c')", ('a' * 64,))
        c.execute("INSERT INTO asset_references(asset_id,owner_type,owner_id,field,created_at) VALUES(1,'publication_version','1','markdown','c')")
    return paths


@pytest.mark.parametrize(
    ("name", "target", "steps"),
    (("calendar", CALENDAR_VERSION, CALENDAR), ("notes", NOTES_VERSION, NOTES), ("publishing", PUBLISHING_VERSION, PUBLISHING), ("assets", ASSETS_VERSION, ASSETS)),
)
def test_domain_migrations_preserve_legacy_rows_and_source_bytes(tmp_path, context, name, target, steps):
    source = _seed_sources(tmp_path / "sources")[name]
    sequence_tables = {"calendar": ("tasks", "topics"), "notes": ("notes", "note_links"), "publishing": ("publications", "publication_versions"), "assets": ("assets", "asset_references")}[name]
    with sqlite3.connect(source) as connection:
        for table in sequence_tables: connection.execute("UPDATE sqlite_sequence SET seq=50 WHERE name=?", (table,))
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    before = _snapshot(source, name)
    staging = tmp_path / "staging" / f"{name}.db"
    staging.parent.mkdir(); shutil.copy2(source, staging)
    context["publication_public_ids"] = {"1": str(uuid4())}
    context["asset_public_ids"] = {"1": str(uuid4())}
    if name == "publishing": context["public_asset_projection"] = ((1, 1, context["asset_public_ids"]["1"]),)

    with sqlite3.connect(staging) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        result = migrate(connection, target_version=target, migrations=steps, context=context)
        assert result.applied_versions == (target,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert all(connection.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()[0] >= 50 for table in sequence_tables)
        repeated = migrate(connection, target_version=target, migrations=steps, context=context)
        assert repeated.changed is False

    assert _snapshot(staging, name) == before
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash


def test_tenant_unique_constraints_replace_global_uniques(tmp_path, context):
    paths = _seed_sources(tmp_path)
    context["publication_public_ids"] = {"1": str(uuid4())}
    context["asset_public_ids"] = {"1": str(uuid4())}
    for name, target, steps in (("calendar", CALENDAR_VERSION, CALENDAR), ("publishing", PUBLISHING_VERSION, PUBLISHING), ("assets", ASSETS_VERSION, ASSETS)):
        with sqlite3.connect(paths[name]) as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            migrate(connection, target_version=target, migrations=steps, context=context)
    other = str(uuid4())
    with sqlite3.connect(paths["calendar"]) as c:
        c.execute("INSERT INTO topics(name,color,created_at,updated_at,workspace_id) VALUES('Topic','#445566','c','u',?)", (other,))
        with pytest.raises(sqlite3.IntegrityError): c.execute("INSERT INTO topics(name,color,created_at,updated_at,workspace_id) VALUES('topic','#445566','c','u',?)", (context["workspace_id"],))
    with sqlite3.connect(paths["publishing"]) as c:
        values = (1,"manual","guide","Other","","draft",1,None,1,None,None,"c","u",other,context["actor_id"],"INTERNAL",None)
        c.execute("INSERT INTO publications VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
        with pytest.raises(sqlite3.IntegrityError): c.execute("UPDATE publications SET workspace_id=? WHERE id=2", (context["workspace_id"],))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE publications SET public_id=? WHERE id=1", (str(uuid4()),))
    with sqlite3.connect(paths["assets"]) as c:
        c.execute("INSERT INTO assets(sha256,media_type,byte_size,storage_key,original_name,created_at,workspace_id,uploaded_by,public_id) VALUES(?, 'image/png',3,'def','b.png','c',?,?,?)", ('a'*64, other, context["actor_id"], str(uuid4())))
        with pytest.raises(sqlite3.IntegrityError): c.execute("UPDATE assets SET workspace_id=? WHERE id=2", (context["workspace_id"],))
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE assets SET public_id=? WHERE id=1", (str(uuid4()),))


def test_failed_domain_migration_rolls_back_and_preserves_source(tmp_path, context):
    source = _seed_sources(tmp_path)["notes"]
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    staging = tmp_path / "failed.db"; shutil.copy2(source, staging)
    bad = dict(context); bad["workspace_id"] = "not-a-uuid"
    with sqlite3.connect(staging) as connection:
        with pytest.raises(Exception):
            migrate(connection, target_version=NOTES_VERSION, migrations=NOTES, context=bad)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "workspace_id" not in {row[1] for row in connection.execute("PRAGMA table_info(notes)")}
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


@pytest.mark.parametrize(("name", "target", "steps", "mapping_key"), (("publishing", PUBLISHING_VERSION, PUBLISHING, "publication_public_ids"), ("assets", ASSETS_VERSION, ASSETS, "asset_public_ids")))
def test_public_id_mapping_must_be_complete(tmp_path, context, name, target, steps, mapping_key):
    source = _seed_sources(tmp_path)[name]
    context[mapping_key] = {}
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(Exception) as raised:
            migrate(connection, target_version=target, migrations=steps, context=context)
        assert raised.value.__cause__ is not None
        assert "complete" in str(raised.value.__cause__)
