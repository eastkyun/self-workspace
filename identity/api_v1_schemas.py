"""Stable Pydantic transport contracts for Workspace API v1."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import Literal


class _V1Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TeamCreateRequest(_V1Model):
    name: str
    description: str = ""
    timezone: str = "Asia/Seoul"


class WorkspaceUpdateRequest(_V1Model):
    name: str | None = None
    description: str | None = None
    timezone: str | None = None


class InvitationRequest(_V1Model):
    email: str
    role_key: str = "member"


class RoleChangeRequest(_V1Model):
    role_key: str


class OwnershipTransferRequest(_V1Model):
    member_id: str


class AppUpdateRequest(_V1Model):
    enabled: bool | None = None
    display_order: int | None = None
    settings: dict[str, object] | None = None


class AppOrderRequest(_V1Model):
    app_keys: tuple[str, ...]


class AppPolicyResponse(_V1Model):
    key: str
    display_name: str
    description: str
    icon: str
    status: str
    enabled: bool
    effective: bool
    display_order: int
    settings: dict[str, object]
    manifest_checksum: str


class RolePolicyResponse(_V1Model):
    id: str
    key: str
    name: str
    description: str
    builtin: bool
    system_role: str | None
    permissions: list[str]


class PermissionOverrideRequest(_V1Model):
    effect: Literal["ALLOW", "DENY"]
