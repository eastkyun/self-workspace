"""Pure authorization policy. No DB, HTTP, or app-specific branches belong here."""

from __future__ import annotations

from .context import PermissionSnapshot


def permission_allowed(
    snapshot: PermissionSnapshot,
    permission_code: str,
    *,
    user_id: str,
    resource_owner_id: str | None = None,
) -> bool:
    """Apply DENY > ALLOW > role and then OWN ownership validation."""

    if permission_code in snapshot.explicit_denies:
        return False
    if permission_code in snapshot.owner_only_permissions and snapshot.role_key != "owner":
        return False
    granted = (
        permission_code in snapshot.explicit_allows
        or permission_code in snapshot.role_permissions
    )
    if not granted:
        return False
    if permission_code.endswith((".update_own", ".delete_own")):
        return resource_owner_id is not None and resource_owner_id == user_id
    return True


def app_allowed(snapshot: PermissionSnapshot, app_key: str, *, user_id: str) -> bool:
    if snapshot.app_id(app_key) is None:
        return False
    read_code = f"{app_key}.read_all"
    # HOME is the sole authenticated system surface without an app permission family.
    if not snapshot.has_declared_read(app_key):
        return app_key == "home"
    return permission_allowed(snapshot, read_code, user_id=user_id)
