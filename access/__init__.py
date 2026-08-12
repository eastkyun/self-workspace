"""Shared workspace authorization boundary."""

from .context import PermissionSnapshot, WorkspaceContext
from .errors import AppNotAvailable, PermissionDenied, WorkspaceNotAvailable
from .service import AuthorizationService

__all__ = [
    "AppNotAvailable",
    "AuthorizationService",
    "PermissionDenied",
    "PermissionSnapshot",
    "WorkspaceContext",
    "WorkspaceNotAvailable",
]
