"""Contracts implemented by database-specific migrations.

Migration callbacks own one SQLite connection and must use ``execute``/
``executemany`` for transactional DDL and DML. They must not commit, roll back,
open a transaction, or attach another database; the runner owns that boundary.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Protocol, TypeAlias, runtime_checkable


MigrationContext: TypeAlias = Mapping[str, object]
class MigrationConnection(Protocol):
    """Restricted SQL surface exposed to trusted repository callbacks.

    This is a capability contract that prevents accidental transaction and
    connection-boundary control. It is not an in-process sandbox for hostile
    Python code.
    """

    def execute(self, sql: str, parameters: Iterable[Any] = ...) -> sqlite3.Cursor: ...
    def executemany(self, sql: str, parameters: Iterable[Iterable[Any]]) -> sqlite3.Cursor: ...


MigrationCallback: TypeAlias = Callable[[MigrationConnection, MigrationContext], None]


@runtime_checkable
class Migration(Protocol):
    """A single, adjacent schema-version transition."""

    from_version: int
    to_version: int

    def apply(
        self, connection: MigrationConnection, context: MigrationContext
    ) -> None: ...

    def verify(
        self, connection: MigrationConnection, context: MigrationContext
    ) -> None: ...


@dataclass(frozen=True, slots=True)
class MigrationStep:
    """Convenient immutable implementation of :class:`Migration`."""

    from_version: int
    to_version: int
    apply: MigrationCallback
    verify: MigrationCallback
