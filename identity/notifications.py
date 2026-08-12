"""Password-reset delivery boundary and development outbox."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PasswordResetMessage:
    recipient: str
    reset_url: str


@dataclass(frozen=True, slots=True)
class WorkspaceInvitationMessage:
    recipient: str
    invitation_url: str


class NotificationProvider(Protocol):
    def send_password_reset(self, message: PasswordResetMessage) -> None: ...

    def send_workspace_invitation(self, message: WorkspaceInvitationMessage) -> None: ...


class LocalOutboxNotificationProvider:
    """Append development deliveries to an owner-readable local outbox."""

    def __init__(self, path: str | Path) -> None:
        candidate = Path(path).expanduser()
        # Canonicalize the directory but deliberately preserve the final
        # component so O_NOFOLLOW can reject a pre-existing symlink.
        self.path = candidate.parent.resolve() / candidate.name

    def send_password_reset(self, message: PasswordResetMessage) -> None:
        self._append({"recipient": message.recipient, "reset_url": message.reset_url})

    def send_workspace_invitation(self, message: WorkspaceInvitationMessage) -> None:
        self._append(
            {"recipient": message.recipient, "invitation_url": message.invitation_url}
        )

    def _append(self, body: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise RuntimeError("local outbox must be a regular file")
            if info.st_nlink != 1:
                raise RuntimeError("local outbox must not have multiple links")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise RuntimeError("local outbox must be owned by the current user")
            os.fchmod(descriptor, 0o600)
            payload = json.dumps(body, sort_keys=True, separators=(",", ":"))
            os.write(descriptor, (payload + "\n").encode())
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class MemoryNotificationProvider:
    """Deterministic provider for tests."""

    def __init__(self) -> None:
        self.messages: list[PasswordResetMessage] = []
        self.invitation_messages: list[WorkspaceInvitationMessage] = []

    def send_password_reset(self, message: PasswordResetMessage) -> None:
        self.messages.append(message)

    def send_workspace_invitation(self, message: WorkspaceInvitationMessage) -> None:
        self.invitation_messages.append(message)
