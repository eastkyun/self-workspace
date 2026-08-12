"""Typed policy failures; HTTP adapters decide the public response body."""


class AuthorizationError(RuntimeError):
    pass


class WorkspaceNotAvailable(AuthorizationError):
    """The active user/membership/workspace tuple does not exist."""


class AppNotAvailable(AuthorizationError):
    """The app is disabled or not visible to this member."""


class PermissionDenied(AuthorizationError):
    """The member exists in the workspace but lacks a capability."""
