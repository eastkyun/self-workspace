"""Immutable request and permission snapshots used by every app policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    user_id: str
    workspace_id: str
    membership_id: str
    role_key: str
    enabled_app_ids: tuple[str, ...]
    authorization_version: int


@dataclass(frozen=True, slots=True)
class PermissionSnapshot:
    """One immutable view of mutable authorization state for one request."""

    workspace_id: str
    membership_id: str
    role_key: str
    version: int
    enabled_apps: tuple[tuple[str, str], ...]
    role_permissions: frozenset[str]
    explicit_allows: frozenset[str]
    explicit_denies: frozenset[str]
    owner_only_permissions: frozenset[str]
    read_required_apps: frozenset[str] = frozenset()

    def app_id(self, app_key: str) -> str | None:
        return next((app_id for key, app_id in self.enabled_apps if key == app_key), None)

    def has_declared_read(self, app_key: str) -> bool:
        return app_key in self.read_required_apps
