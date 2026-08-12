"""Opaque browser-session authentication for the identity database."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import sqlite3
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

from argon2 import PasswordHasher

from identity.database import database_connection
from identity.foundation import (
    BUILTIN_ROLES,
    FOUNDATION_APPS,
    foundation_app_checksum,
    permission_code,
    permission_id,
    role_permission_codes,
)
from identity.notifications import NotificationProvider, PasswordResetMessage
from identity.passwords import (
    PRODUCTION_PASSWORD_HASHER,
    hash_password,
    verify_password,
)
from identity.primitives import new_uuid, normalize_email, utc_now


logger = logging.getLogger(__name__)


class AuthenticationError(RuntimeError):
    """Stable authentication failure without credential disclosure."""


class AuthenticationRequired(AuthenticationError):
    pass


class AuthenticationRateLimited(AuthenticationError):
    pass


class AuthenticationConflict(AuthenticationError):
    pass


class AuthenticationForbidden(AuthenticationError):
    pass


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    idle_ttl: timedelta = timedelta(hours=8)
    absolute_ttl: timedelta = timedelta(days=7)
    max_active_per_user: int = 10
    last_seen_interval: timedelta = timedelta(minutes=5)
    reset_ttl: timedelta = timedelta(minutes=30)


@dataclass(frozen=True, slots=True)
class SessionCredentials:
    session_token: str
    csrf_token: str
    session_id: str
    user_id: str
    workspace_id: str


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    id: str
    email: str
    display_name: str
    session_id: str
    last_workspace_id: str | None


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _token() -> str:
    # 32 bytes is exactly 256 bits of CSPRNG entropy.
    return secrets.token_urlsafe(32)


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class LoginRateLimiter:
    """Bounded account and client-IP failure windows, injectable in tests."""

    def __init__(self, *, window: timedelta, max_failures: int) -> None:
        self.window = window
        self.max_failures = max_failures
        self._account: dict[str, deque[datetime]] = defaultdict(deque)
        self._client: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _trim(self, values: deque[datetime], now: datetime) -> None:
        threshold = now - self.window
        while values and values[0] <= threshold:
            values.popleft()

    def check(self, account: str, client_ip: str, now: datetime) -> None:
        with self._lock:
            for values in (self._account[account], self._client[client_ip]):
                self._trim(values, now)
                if len(values) >= self.max_failures:
                    raise AuthenticationRateLimited("authentication temporarily unavailable")

    def failed(self, account: str, client_ip: str, now: datetime) -> None:
        with self._lock:
            for values in (self._account[account], self._client[client_ip]):
                self._trim(values, now)
                values.append(now)

    def succeeded(self, account: str) -> None:
        with self._lock:
            self._account.pop(account, None)


class AuthenticationService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        policy: SessionPolicy = SessionPolicy(),
        rate_limiter: LoginRateLimiter | None = None,
        notification_provider: NotificationProvider,
        public_origin: str,
        clock: Callable[[], datetime] = utc_now,
        hasher: PasswordHasher = PRODUCTION_PASSWORD_HASHER,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.policy = policy
        self.rate_limiter = rate_limiter or LoginRateLimiter(
            window=timedelta(minutes=15), max_failures=5
        )
        self.notification_provider = notification_provider
        self.public_origin = public_origin.rstrip("/")
        self.clock = clock
        self.hasher = hasher

    def _create_session(self, tx, user_id: str, workspace_id: str, now: datetime) -> SessionCredentials:
        session_token, csrf_token, session_id = _token(), _token(), new_uuid()
        tx.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)",
            (
                session_id,
                user_id,
                _hash_token(session_token),
                _hash_token(csrf_token),
                workspace_id,
                _timestamp(now + self.policy.absolute_ttl),
                _timestamp(now),
                _timestamp(now),
            ),
        )
        idle_threshold = _timestamp(now - self.policy.idle_ttl)
        active = tx.query(
            "SELECT id FROM sessions WHERE user_id=? AND revoked_at IS NULL "
            "AND expires_at>? AND last_seen_at>? ORDER BY created_at DESC, rowid DESC",
            (user_id, _timestamp(now), idle_threshold),
        )
        for row in active[self.policy.max_active_per_user :]:
            tx.execute(
                "UPDATE sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (_timestamp(now), row["id"]),
            )
        return SessionCredentials(session_token, csrf_token, session_id, user_id, workspace_id)

    @staticmethod
    def _audit_workspace(tx, user_id: str, preferred: str | None = None) -> str | None:
        row = tx.execute(
            "SELECT member.workspace_id FROM workspace_members AS member "
            "JOIN workspaces AS workspace ON workspace.id=member.workspace_id "
            "WHERE member.user_id=? AND member.status='ACTIVE' AND workspace.status='ACTIVE' "
            "ORDER BY CASE WHEN member.workspace_id=? THEN 0 ELSE 1 END, member.joined_at LIMIT 1",
            (user_id, preferred or ""),
        ).fetchone()
        return row["workspace_id"] if row is not None else None

    def signup(self, email: str, password: str, display_name: str | None = None) -> SessionCredentials:
        normalized = normalize_email(email)
        if not password:
            raise AuthenticationConflict("invalid signup")
        now = self.clock()
        stamp = _timestamp(now)
        user_id, workspace_id, membership_id = new_uuid(), new_uuid(), new_uuid()
        name = (display_name or normalized.partition("@")[0] or "User").strip()
        if not name:
            raise AuthenticationConflict("invalid signup")
        try:
            password_hash = hash_password(password, hasher=self.hasher)
            with database_connection(self.database_path) as tx:
                tx.execute(
                    "INSERT INTO users VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, NULL)",
                    (user_id, normalized, name, password_hash, stamp, stamp),
                )
                tx.execute(
                    "INSERT INTO workspaces VALUES (?, 'PERSONAL', ?, '', NULL, "
                    "'Asia/Seoul', 'ACTIVE', ?, ?, ?, NULL)",
                    (workspace_id, f"{name}'s Workspace", user_id, stamp, stamp),
                )
                role_ids: dict[str, str] = {}
                for role_key, role_name, system_role in BUILTIN_ROLES:
                    role_ids[role_key] = new_uuid()
                    tx.execute(
                        "INSERT INTO roles VALUES (?, ?, ?, ?, '', 1, ?, 'ACTIVE', ?, ?, ?)",
                        (role_ids[role_key], workspace_id, role_key, role_name, system_role, user_id, stamp, stamp),
                    )
                tx.execute(
                    "INSERT INTO workspace_members VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?)",
                    (membership_id, workspace_id, user_id, role_ids["owner"], stamp, stamp, stamp),
                )
                for app in FOUNDATION_APPS:
                    tx.execute(
                        "INSERT OR IGNORE INTO apps VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'ACTIVE', ?, ?, ?)",
                        (app.id, app.key, app.name, app.description, app.icon, app.category, app.version, foundation_app_checksum(app), stamp, stamp),
                    )
                    tx.execute(
                        "INSERT INTO workspace_apps VALUES (?, ?, ?, ?, '{}', ?, ?, ?)",
                        (workspace_id, app.id, int(app.default_enabled), app.display_order, user_id, stamp, stamp),
                    )
                    for permission in app.permissions:
                        code = permission_code(app, permission)
                        tx.execute(
                            "INSERT OR IGNORE INTO permissions VALUES (?, ?, ?, ?, '', ?, ?, ?)",
                            (permission_id(code), code, app.id, code, int(code in {"workspace.ownership.transfer", "workspace.delete"}), stamp, stamp),
                        )
                for role_key, role_id in role_ids.items():
                    tx.executemany(
                        "INSERT INTO role_permissions VALUES (?, ?, 'ALLOW', ?)",
                        ((role_id, permission_id(code), stamp) for code in sorted(role_permission_codes(role_key))),
                    )
                credentials = self._create_session(tx, user_id, workspace_id, now)
                tx.execute(
                    "INSERT INTO audit_logs VALUES (?, ?, 'auth.signup', 'user', ?, ?, ?)",
                    (workspace_id, user_id, user_id, json.dumps({"membership_id": membership_id}, separators=(",", ":"), sort_keys=True), stamp),
                )
        except sqlite3.IntegrityError as error:
            raise AuthenticationConflict("account cannot be created") from error
        return credentials

    def login(self, email: str, password: str, client_ip: str, presented_token: str | None = None) -> SessionCredentials:
        try:
            normalized = normalize_email(email)
        except ValueError:
            normalized = "invalid"
        now = self.clock()
        try:
            self.rate_limiter.check(normalized, client_ip, now)
        except AuthenticationRateLimited:
            logger.warning("authentication rate limit exceeded", extra={"security_event": "auth.rate_limited"})
            raise
        with database_connection(self.database_path) as tx:
            user = tx.execute(
                "SELECT id, password_hash FROM users WHERE email_normalized=? AND status='ACTIVE'",
                (normalized,),
            ).fetchone()
            if user is None or not verify_password(user["password_hash"], password, hasher=self.hasher):
                self.rate_limiter.failed(normalized, client_ip, now)
                logger.warning("authentication failed", extra={"security_event": "auth.login_failed"})
                raise AuthenticationRequired("invalid credentials")
            membership = tx.execute(
                "SELECT member.workspace_id FROM workspace_members AS member "
                "JOIN workspaces AS workspace ON workspace.id=member.workspace_id "
                "WHERE member.user_id=? AND member.status='ACTIVE' AND workspace.status='ACTIVE' "
                "ORDER BY CASE workspace.type WHEN 'PERSONAL' THEN 0 ELSE 1 END, member.joined_at LIMIT 1",
                (user["id"],),
            ).fetchone()
            if membership is None:
                raise AuthenticationRequired("invalid credentials")
            if presented_token:
                tx.execute(
                    "UPDATE sessions SET revoked_at=? WHERE token_hash=? AND revoked_at IS NULL",
                    (_timestamp(now), _hash_token(presented_token)),
                )
            if self.hasher.check_needs_rehash(user["password_hash"]):
                tx.execute(
                    "UPDATE users SET password_hash=?, updated_at=? WHERE id=?",
                    (hash_password(password, hasher=self.hasher), _timestamp(now), user["id"]),
                )
            credentials = self._create_session(tx, user["id"], membership["workspace_id"], now)
            tx.execute(
                "INSERT INTO audit_logs VALUES (?, ?, 'auth.login', 'session', ?, '{}', ?)",
                (membership["workspace_id"], user["id"], credentials.session_id, _timestamp(now)),
            )
        self.rate_limiter.succeeded(normalized)
        return credentials

    def authenticate(self, session_token: str | None) -> AuthenticatedUser:
        if not session_token:
            raise AuthenticationRequired("authentication required")
        token_hash = _hash_token(session_token)
        now = self.clock()
        with database_connection(self.database_path) as tx:
            row = tx.execute(
                "SELECT session.id AS session_id, session.user_id, session.last_workspace_id, "
                "session.expires_at, session.last_seen_at, user.email_normalized, user.display_name "
                "FROM sessions AS session JOIN users AS user ON user.id=session.user_id "
                "WHERE session.token_hash=? AND session.revoked_at IS NULL AND user.status='ACTIVE'",
                (token_hash,),
            ).fetchone()
            if row is None:
                raise AuthenticationRequired("authentication required")
            expired = now >= _parse_timestamp(row["expires_at"])
            idle = now - _parse_timestamp(row["last_seen_at"]) >= self.policy.idle_ttl
            if expired or idle:
                tx.execute(
                    "UPDATE sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                    (_timestamp(now), row["session_id"]),
                )
                raise AuthenticationRequired("authentication required")
            if now - _parse_timestamp(row["last_seen_at"]) >= self.policy.last_seen_interval:
                tx.execute(
                    "UPDATE sessions SET last_seen_at=? WHERE id=?",
                    (_timestamp(now), row["session_id"]),
                )
            return AuthenticatedUser(
                row["user_id"], row["email_normalized"], row["display_name"],
                row["session_id"], row["last_workspace_id"],
            )

    def validate_csrf(self, session_id: str, csrf_token: str | None) -> None:
        if not csrf_token:
            raise AuthenticationForbidden("CSRF validation failed")
        with database_connection(self.database_path) as tx:
            row = tx.execute(
                "SELECT csrf_secret_hash FROM sessions WHERE id=? AND revoked_at IS NULL",
                (session_id,),
            ).fetchone()
        if row is None or not hmac.compare_digest(row["csrf_secret_hash"], _hash_token(csrf_token)):
            raise AuthenticationForbidden("CSRF validation failed")

    def logout(self, user: AuthenticatedUser) -> None:
        with database_connection(self.database_path) as tx:
            tx.execute(
                "UPDATE sessions SET revoked_at=? WHERE id=? AND revoked_at IS NULL",
                (_timestamp(self.clock()), user.session_id),
            )
            workspace_id = self._audit_workspace(tx, user.id, user.last_workspace_id)
            if workspace_id is not None:
                tx.execute(
                    "INSERT INTO audit_logs VALUES (?, ?, 'auth.logout', 'session', ?, '{}', ?)",
                    (workspace_id, user.id, user.session_id, _timestamp(self.clock())),
                )

    def update_profile(self, user: AuthenticatedUser, *, display_name: str | None, disable: bool) -> AuthenticatedUser:
        now = _timestamp(self.clock())
        with database_connection(self.database_path) as tx:
            if display_name is not None:
                name = display_name.strip()
                if not name:
                    raise AuthenticationConflict("invalid profile")
                tx.execute("UPDATE users SET display_name=?, updated_at=? WHERE id=?", (name, now, user.id))
            if disable:
                try:
                    tx.execute(
                        "UPDATE users SET status='DISABLED', disabled_at=?, updated_at=? WHERE id=?",
                        (now, now, user.id),
                    )
                except sqlite3.IntegrityError as error:
                    raise AuthenticationConflict("account cannot be disabled") from error
            workspace_id = self._audit_workspace(tx, user.id, user.last_workspace_id)
            if workspace_id is not None and (display_name is not None or disable):
                tx.execute(
                    "INSERT INTO audit_logs VALUES (?, ?, ?, 'user', ?, '{}', ?)",
                    (workspace_id, user.id, "auth.account.disabled" if disable else "auth.profile.updated", user.id, now),
                )
        if disable:
            return user
        return self.authenticate_by_id(user.session_id)

    def authenticate_by_id(self, session_id: str) -> AuthenticatedUser:
        with database_connection(self.database_path) as tx:
            row = tx.execute(
                "SELECT user.id, user.email_normalized, user.display_name, session.last_workspace_id "
                "FROM sessions AS session JOIN users AS user ON user.id=session.user_id "
                "WHERE session.id=? AND session.revoked_at IS NULL AND user.status='ACTIVE'",
                (session_id,),
            ).fetchone()
        if row is None:
            raise AuthenticationRequired("authentication required")
        return AuthenticatedUser(row["id"], row["email_normalized"], row["display_name"], session_id, row["last_workspace_id"])

    def change_password(self, user: AuthenticatedUser, current_password: str, new_password: str) -> None:
        now = _timestamp(self.clock())
        with database_connection(self.database_path) as tx:
            row = tx.execute("SELECT password_hash FROM users WHERE id=? AND status='ACTIVE'", (user.id,)).fetchone()
            if row is None or not verify_password(row["password_hash"], current_password, hasher=self.hasher):
                raise AuthenticationRequired("invalid credentials")
            if not new_password:
                raise AuthenticationConflict("invalid password")
            tx.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?", (hash_password(new_password, hasher=self.hasher), now, user.id))
            tx.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (now, user.id))
            workspace_id = self._audit_workspace(tx, user.id, user.last_workspace_id)
            if workspace_id is not None:
                tx.execute("INSERT INTO audit_logs VALUES (?, ?, 'auth.password.changed', 'user', ?, '{}', ?)", (workspace_id, user.id, user.id, now))

    def request_password_reset(self, email: str) -> None:
        try:
            normalized = normalize_email(email)
        except ValueError:
            return
        now = self.clock()
        raw_token = _token()
        with database_connection(self.database_path) as tx:
            user = tx.execute("SELECT id FROM users WHERE email_normalized=? AND status='ACTIVE'", (normalized,)).fetchone()
            if user is None:
                return
            tx.execute(
                "UPDATE password_reset_tokens SET consumed_at=? WHERE user_id=? AND consumed_at IS NULL",
                (_timestamp(now), user["id"]),
            )
            tx.execute(
                "INSERT INTO password_reset_tokens VALUES (?, ?, ?, ?, NULL, ?)",
                (new_uuid(), user["id"], _hash_token(raw_token), _timestamp(now + self.policy.reset_ttl), _timestamp(now)),
            )
        reset_url = f"{self.public_origin}/reset-password?token={quote(raw_token, safe='')}"
        try:
            self.notification_provider.send_password_reset(PasswordResetMessage(normalized, reset_url))
        except Exception:
            # Account existence must remain undisclosed even when the delivery
            # boundary is unavailable. Record a secret-free workspace event.
            logger.warning("password reset delivery failed", extra={"security_event": "auth.reset_delivery_failed"})
            with database_connection(self.database_path) as tx:
                workspace_id = self._audit_workspace(tx, user["id"])
                if workspace_id is not None:
                    tx.execute("INSERT INTO audit_logs VALUES (?, ?, 'auth.password_reset.delivery_failed', 'user', ?, '{}', ?)", (workspace_id, user["id"], user["id"], _timestamp(now)))

    def reset_password(self, raw_token: str, new_password: str) -> None:
        if not raw_token or not new_password:
            raise AuthenticationRequired("invalid or expired reset token")
        now = self.clock()
        with database_connection(self.database_path) as tx:
            row = tx.execute(
                "SELECT id, user_id, expires_at FROM password_reset_tokens "
                "WHERE token_hash=? AND consumed_at IS NULL",
                (_hash_token(raw_token),),
            ).fetchone()
            if row is None or now >= _parse_timestamp(row["expires_at"]):
                raise AuthenticationRequired("invalid or expired reset token")
            stamp = _timestamp(now)
            changed = tx.execute(
                "UPDATE password_reset_tokens SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                (stamp, row["id"]),
            ).rowcount
            if changed != 1:
                raise AuthenticationRequired("invalid or expired reset token")
            tx.execute("UPDATE users SET password_hash=?, updated_at=? WHERE id=?", (hash_password(new_password, hasher=self.hasher), stamp, row["user_id"]))
            tx.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (stamp, row["user_id"]))
            workspace_id = self._audit_workspace(tx, row["user_id"])
            if workspace_id is not None:
                tx.execute("INSERT INTO audit_logs VALUES (?, ?, 'auth.password.reset', 'user', ?, '{}', ?)", (workspace_id, row["user_id"], row["user_id"], stamp))
