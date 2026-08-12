"""Fail-soft, non-authoritative workspace activity recording."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from access.context import WorkspaceContext
from identity.database import database_connection


logger = logging.getLogger(__name__)


class ActivitySink(Protocol):
    def record(
        self, context: WorkspaceContext, *, app_key: str, action: str,
        resource_type: str, resource_id: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None: ...


class NullActivitySink:
    def record(self, context: WorkspaceContext, **event: object) -> None:
        return None


class SqliteActivitySink:
    """Writes only display-safe metadata and never fails the domain operation."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).resolve()

    def record(
        self, context: WorkspaceContext, *, app_key: str, action: str,
        resource_type: str, resource_id: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        try:
            now = datetime.now(UTC)
            stamp = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
            cutoff = (now - timedelta(days=90)).isoformat(timespec="microseconds").replace("+00:00", "Z")
            safe_metadata = {
                key: value for key, value in (metadata or {}).items()
                if key in {"status", "operation"} and isinstance(value, str)
            }
            with database_connection(self.database_path) as tx:
                tx.execute(
                    "INSERT INTO activity_logs(workspace_id,actor_user_id,app_key,action,"
                    "resource_type,resource_id,metadata_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (context.workspace_id, context.user_id, app_key, action,
                     resource_type, resource_id,
                     json.dumps(safe_metadata, sort_keys=True, separators=(",", ":")), stamp),
                )
                tx.execute("DELETE FROM activity_logs WHERE created_at < ?", (cutoff,))
        except (OSError, RuntimeError, sqlite3.Error) as error:
            # Activity is a projection. Domain commits must never depend on it.
            logger.warning(
                "workspace_activity_record_failed",
                extra={
                    "workspace_id": context.workspace_id,
                    "app_key": app_key,
                    "action": action,
                    "error_type": type(error).__name__,
                },
            )
            return None


__all__ = ["ActivitySink", "NullActivitySink", "SqliteActivitySink"]
