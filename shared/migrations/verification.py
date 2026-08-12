"""Common SQLite migration verification hooks."""

from __future__ import annotations

import sqlite3


class DatabaseVerificationError(RuntimeError):
    """The SQLite file failed an integrity or foreign-key check."""


class DatabaseBoundaryError(RuntimeError):
    """A migration connection crosses the required single-database boundary."""


def get_user_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None or not isinstance(row[0], int):
        raise DatabaseVerificationError("database has no valid PRAGMA user_version")
    return row[0]


def assert_single_database(connection: sqlite3.Connection) -> None:
    """Reject connections with an attached persistent database.

    SQLite's private ``temp`` schema is not a separate artifact and is allowed.
    Every persistent schema used by a migration must be the connection's
    ``main`` database.
    """

    databases = connection.execute("PRAGMA database_list").fetchall()
    unexpected = [str(row[1]) for row in databases if row[1] not in {"main", "temp"}]
    if unexpected:
        names = ", ".join(sorted(unexpected))
        raise DatabaseBoundaryError(f"attached databases are not allowed: {names}")


def verify_database(connection: sqlite3.Connection) -> None:
    """Run the common integrity and referential-integrity checks."""

    assert_single_database(connection)
    quick_check = connection.execute("PRAGMA quick_check").fetchall()
    messages = [str(row[0]) for row in quick_check]
    if messages != ["ok"]:
        detail = "; ".join(messages) or "no result"
        raise DatabaseVerificationError(f"database quick_check failed: {detail}")

    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
    if foreign_key_errors:
        raise DatabaseVerificationError(
            "database foreign_key_check failed: "
            f"{len(foreign_key_errors)} violation(s)"
        )
