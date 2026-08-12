"""Transactional runner for adjacent SQLite schema migrations."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

from .contract import Migration, MigrationContext
from .verification import assert_single_database, get_user_version, verify_database


class MigrationError(RuntimeError):
    """A migration definition or execution failed."""


_CONNECTION_REGISTRY: dict[object, sqlite3.Connection] = {}


class _RestrictedConnection:
    """Do not expose transaction, authorizer, attach helpers, or raw connection."""

    __slots__ = ("__token",)

    def __init__(self, connection: sqlite3.Connection) -> None:
        token = object()
        self.__token = token
        _CONNECTION_REGISTRY[token] = connection

    def execute(self, sql: str, parameters=()) -> sqlite3.Cursor:
        return _CONNECTION_REGISTRY[self.__token].execute(sql, parameters)

    def executemany(self, sql: str, parameters) -> sqlite3.Cursor:
        return _CONNECTION_REGISTRY[self.__token].executemany(sql, parameters)



def _release_connection(facade: _RestrictedConnection) -> None:
    token = object.__getattribute__(facade, "_RestrictedConnection__token")
    _CONNECTION_REGISTRY.pop(token, None)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    from_version: int
    to_version: int
    applied_versions: tuple[int, ...]

    @property
    def changed(self) -> bool:
        return bool(self.applied_versions)


def _validated_steps(migrations: Iterable[Migration]) -> dict[int, Migration]:
    steps: dict[int, Migration] = {}
    for migration in migrations:
        from_version = migration.from_version
        to_version = migration.to_version
        if (
            not isinstance(from_version, int)
            or isinstance(from_version, bool)
            or from_version < 0
            or not isinstance(to_version, int)
            or isinstance(to_version, bool)
            or to_version != from_version + 1
        ):
            raise MigrationError(
                "migrations must define adjacent non-negative integer versions"
            )
        if from_version in steps:
            raise MigrationError(f"duplicate migration from version {from_version}")
        steps[from_version] = migration
    return steps


def _deny_boundary_changes(
    action: int,
    _arg1: str | None,
    _arg2: str | None,
    _database: str | None,
    _trigger: str | None,
) -> int:
    if action in {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_TRANSACTION}:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


_WRITE_ACTIONS = {
    sqlite3.SQLITE_INSERT,
    sqlite3.SQLITE_UPDATE,
    sqlite3.SQLITE_DELETE,
    sqlite3.SQLITE_CREATE_INDEX,
    sqlite3.SQLITE_CREATE_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_INDEX,
    sqlite3.SQLITE_CREATE_TEMP_TABLE,
    sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
    sqlite3.SQLITE_CREATE_TEMP_VIEW,
    sqlite3.SQLITE_CREATE_TRIGGER,
    sqlite3.SQLITE_CREATE_VIEW,
    sqlite3.SQLITE_DROP_INDEX,
    sqlite3.SQLITE_DROP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_INDEX,
    sqlite3.SQLITE_DROP_TEMP_TABLE,
    sqlite3.SQLITE_DROP_TEMP_TRIGGER,
    sqlite3.SQLITE_DROP_TEMP_VIEW,
    sqlite3.SQLITE_DROP_TRIGGER,
    sqlite3.SQLITE_DROP_VIEW,
    sqlite3.SQLITE_ALTER_TABLE,
    sqlite3.SQLITE_REINDEX,
}


def _deny_verification_changes(
    action: int,
    arg1: str | None,
    arg2: str | None,
    database: str | None,
    trigger: str | None,
) -> int:
    boundary_result = _deny_boundary_changes(action, arg1, arg2, database, trigger)
    read_pragmas_with_arguments = {
        "foreign_key_check", "foreign_key_list", "index_info", "index_list",
        "index_xinfo", "integrity_check", "quick_check", "table_info", "table_xinfo",
    }
    write_pragma = action == sqlite3.SQLITE_PRAGMA and arg2 is not None and (
        arg1 is None or arg1.lower() not in read_pragmas_with_arguments
    )
    if boundary_result == sqlite3.SQLITE_DENY or action in _WRITE_ACTIONS or write_pragma:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _apply_step(
    connection: sqlite3.Connection,
    migration: Migration,
    context: MigrationContext,
) -> None:
    callback_connection: _RestrictedConnection | None = None
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.set_authorizer(_deny_boundary_changes)
        callback_connection = _RestrictedConnection(connection)
        migration.apply(callback_connection, context)
        assert_single_database(connection)
        if not connection.in_transaction:
            raise MigrationError("migration callback ended the runner transaction")
        connection.execute(f"PRAGMA user_version = {migration.to_version}")
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        connection.set_authorizer(_deny_verification_changes)
        migration.verify(callback_connection, context)
        connection.set_authorizer(_deny_verification_changes)
        assert_single_database(connection)
        if get_user_version(connection) != migration.to_version:
            raise MigrationError("migration verification changed PRAGMA user_version")
        if connection.execute("PRAGMA application_id").fetchone()[0] != application_id:
            raise MigrationError("migration verification changed PRAGMA application_id")
        verify_database(connection)
        if not connection.in_transaction:
            raise MigrationError("migration verification ended the runner transaction")
        connection.set_authorizer(None)
        connection.commit()
    except BaseException as error:
        connection.set_authorizer(None)
        if connection.in_transaction:
            connection.rollback()
        for _sequence, name, _path in connection.execute("PRAGMA database_list").fetchall():
            if name not in {"main", "temp"}:
                connection.execute(f'DETACH DATABASE "{name.replace(chr(34), chr(34) * 2)}"')
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(error, MigrationError):
            raise
        raise MigrationError(
            f"migration v{migration.from_version} to v{migration.to_version} failed"
        ) from error
    finally:
        if callback_connection is not None:
            _release_connection(callback_connection)


def _verify_current(
    connection: sqlite3.Connection,
    migration: Migration,
    context: MigrationContext,
) -> None:
    """Verify an already-current schema without allowing persistent mutation."""

    callback_connection: _RestrictedConnection | None = None
    try:
        connection.execute("BEGIN")
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        connection.set_authorizer(_deny_verification_changes)
        callback_connection = _RestrictedConnection(connection)
        migration.verify(callback_connection, context)
        connection.set_authorizer(_deny_verification_changes)
        assert_single_database(connection)
        if get_user_version(connection) != migration.to_version:
            raise MigrationError("migration verification changed PRAGMA user_version")
        if connection.execute("PRAGMA application_id").fetchone()[0] != application_id:
            raise MigrationError("migration verification changed PRAGMA application_id")
        verify_database(connection)
        if not connection.in_transaction:
            raise MigrationError("migration verification ended the runner transaction")
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(error, MigrationError):
            raise
        raise MigrationError(
            f"verification for database version {migration.to_version} failed"
        ) from error
    finally:
        connection.set_authorizer(None)
        if connection.in_transaction:
            connection.rollback()
        for _sequence, name, _path in connection.execute("PRAGMA database_list").fetchall():
            if name not in {"main", "temp"}:
                connection.execute(f'DETACH DATABASE "{name.replace(chr(34), chr(34) * 2)}"')
        if callback_connection is not None:
            _release_connection(callback_connection)


def migrate(
    connection: sqlite3.Connection,
    *,
    target_version: int,
    migrations: Iterable[Migration],
    context: MigrationContext | None = None,
) -> MigrationResult:
    """Migrate one SQLite connection to ``target_version``.

    Each version transition, its version update, domain verification, common
    integrity checks, and foreign-key checks run in one runner-owned transaction.
    Completed earlier transitions remain committed if a later transition is
    interrupted; the interrupted transition is fully rolled back.
    """

    if (
        not isinstance(target_version, int)
        or isinstance(target_version, bool)
        or target_version < 0
    ):
        raise MigrationError("target_version must be a non-negative integer")
    if connection.in_transaction:
        raise MigrationError("migration requires a connection with no active transaction")

    steps = _validated_steps(migrations)
    values: MigrationContext = {} if context is None else context
    assert_single_database(connection)
    verify_database(connection)
    starting_version = get_user_version(connection)
    if starting_version > target_version:
        raise MigrationError(
            f"database version {starting_version} is newer than target {target_version}"
        )

    planned: list[Migration] = []
    planned_version = starting_version
    while planned_version < target_version:
        migration = steps.get(planned_version)
        if migration is None:
            raise MigrationError(f"no migration from version {planned_version}")
        planned.append(migration)
        planned_version = migration.to_version

    current_version = starting_version
    applied: list[int] = []
    for migration in planned:
        _apply_step(connection, migration, values)
        current_version = migration.to_version
        applied.append(current_version)

    # Repeat runs do not execute apply again, but still prove the current schema
    # and file satisfy both the domain and common verification contracts.
    if not applied and current_version > 0:
        current_step = steps.get(current_version - 1)
        if current_step is None or current_step.to_version != current_version:
            raise MigrationError(f"no verifier for database version {current_version}")
        _verify_current(connection, current_step, values)
    verify_database(connection)
    if get_user_version(connection) != target_version:
        raise MigrationError("migration did not reach the requested user_version")

    return MigrationResult(starting_version, current_version, tuple(applied))
