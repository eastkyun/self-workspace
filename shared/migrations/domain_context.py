"""Validation helpers shared by offline workspace-aware domain migrations."""

from __future__ import annotations

from uuid import UUID

from .contract import MigrationContext


def required_uuid(context: MigrationContext, key: str) -> str:
    value = context.get(key)
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except ValueError as error:
        raise ValueError(f"migration context {key} must be a canonical UUID") from error
    return value


def columns(connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def index_columns(connection, table: str) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for row in connection.execute(f'PRAGMA index_list("{table}")'):
        if row[2]:
            result.add(tuple(str(item[2]) for item in connection.execute(f'PRAGMA index_info("{row[1]}")')))
    return result


def all_index_columns(connection, table: str) -> set[tuple[str, ...]]:
    return {
        tuple(str(item[2]) for item in connection.execute(f'PRAGMA index_info("{row[1]}")'))
        for row in connection.execute(f'PRAGMA index_list("{table}")')
    }


def assert_backfilled(connection, table: str, fields: tuple[str, ...], workspace_id: str, actor_id: str | None = None) -> None:
    for field in fields:
        expected = actor_id if field in {"created_by", "updated_by", "uploaded_by"} else workspace_id
        count = connection.execute(
            f'SELECT COUNT(*) FROM "{table}" WHERE "{field}" IS NULL OR "{field}" != ?',
            (expected,),
        ).fetchone()[0]
        if count:
            raise RuntimeError(f"{table}.{field} was not fully backfilled")


def sequence_value(connection, table: str) -> int:
    row = connection.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
    return 0 if row is None else int(row[0])


def restore_sequence(connection, table: str, value: int) -> None:
    current = sequence_value(connection, table)
    wanted = max(current, value)
    if connection.execute("SELECT 1 FROM sqlite_sequence WHERE name=?", (table,)).fetchone():
        connection.execute("UPDATE sqlite_sequence SET seq=? WHERE name=?", (wanted, table))
    else:
        connection.execute("INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)", (table, wanted))
