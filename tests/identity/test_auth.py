from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from identity.auth import (
    AuthenticationRateLimited,
    AuthenticationRequired,
    AuthenticationService,
    LoginRateLimiter,
    SessionPolicy,
)
from identity.database import initialize_database
from identity.notifications import LocalOutboxNotificationProvider, MemoryNotificationProvider, PasswordResetMessage
from identity.passwords import CHEAPEST_TEST_PASSWORD_HASHER
from identity.router import _client_ip, create_auth_router
from shared.config import load_config
from shared.errors import install_exception_handlers
from shared.security_logging import AuthTokenRedactionFilter


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 3, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def auth(tmp_path):
    database = initialize_database(tmp_path / "identity.db")
    clock = Clock()
    notifications = MemoryNotificationProvider()
    service = AuthenticationService(
        database,
        policy=SessionPolicy(
            idle_ttl=timedelta(hours=8),
            absolute_ttl=timedelta(days=7),
            max_active_per_user=10,
            last_seen_interval=timedelta(minutes=5),
            reset_ttl=timedelta(minutes=30),
        ),
        rate_limiter=LoginRateLimiter(window=timedelta(minutes=15), max_failures=5),
        notification_provider=notifications,
        public_origin="http://testserver",
        clock=clock,
        hasher=CHEAPEST_TEST_PASSWORD_HASHER,
    )
    return service, database, clock, notifications


def _app(service, database):
    config = load_config({"APP_ENV": "test", "IDENTITY_DB_PATH": str(database)})
    app = FastAPI()
    install_exception_handlers(app)
    app.include_router(create_auth_router(service, config))
    return app, config


def test_signup_is_atomic_and_stores_hashes_not_secrets(auth):
    service, database, _, _ = auth
    credentials = service.signup(" User@Example.COM ", "correct horse", "User")

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM workspaces WHERE type='PERSONAL'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM workspace_members WHERE status='ACTIVE'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM workspace_apps").fetchone()[0] == 7
        session = connection.execute("SELECT token_hash, csrf_secret_hash FROM sessions").fetchone()
        assert session["token_hash"] == hashlib.sha256(credentials.session_token.encode()).hexdigest()
        assert session["csrf_secret_hash"] == hashlib.sha256(credentials.csrf_token.encode()).hexdigest()
        dump = database.read_bytes()
        assert credentials.session_token.encode() not in dump
        assert credentials.csrf_token.encode() not in dump
        assert b"correct horse" not in dump


def test_session_idle_absolute_expiry_last_seen_throttle_rotation_and_limit(auth):
    service, database, clock, _ = auth
    first = service.signup("user@example.com", "password")
    before = service.authenticate(first.session_token)
    clock.now += timedelta(minutes=4)
    service.authenticate(first.session_token)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT last_seen_at FROM sessions WHERE id=?", (before.session_id,)).fetchone()[0] == "2026-08-03T00:00:00Z"
    clock.now += timedelta(minutes=2)
    service.authenticate(first.session_token)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT last_seen_at FROM sessions WHERE id=?", (before.session_id,)).fetchone()[0] == "2026-08-03T00:06:00Z"

    rotated = service.login("user@example.com", "password", "127.0.0.1", first.session_token)
    with pytest.raises(AuthenticationRequired):
        service.authenticate(first.session_token)
    assert service.authenticate(rotated.session_token).id == rotated.user_id

    for _ in range(11):
        service.login("user@example.com", "password", "127.0.0.1")
        clock.now += timedelta(seconds=1)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM sessions WHERE revoked_at IS NULL").fetchone()[0] == 10

    clock.now += timedelta(hours=8, seconds=1)
    with pytest.raises(AuthenticationRequired):
        service.authenticate(rotated.session_token)


def test_login_rate_limit_applies_to_account_and_client_ip(auth):
    service, _, clock, _ = auth
    service.signup("user@example.com", "password")
    for _ in range(5):
        with pytest.raises(AuthenticationRequired):
            service.login("user@example.com", "wrong", "192.0.2.1")
    with pytest.raises(AuthenticationRateLimited):
        service.login("user@example.com", "password", "192.0.2.2")
    with pytest.raises(AuthenticationRateLimited):
        service.login("other@example.com", "password", "192.0.2.1")
    clock.now += timedelta(minutes=16)
    assert service.login("user@example.com", "password", "192.0.2.2")


def test_reset_request_is_nondisclosing_and_token_is_one_use(auth):
    service, database, _, notifications = auth
    active = service.signup("user@example.com", "old password")
    service.request_password_reset("missing@example.com")
    assert notifications.messages == []
    service.request_password_reset("user@example.com")
    assert len(notifications.messages) == 1
    token = parse_qs(urlsplit(notifications.messages[0].reset_url).query)["token"][0]
    with sqlite3.connect(database) as connection:
        stored = connection.execute("SELECT token_hash FROM password_reset_tokens").fetchone()[0]
        assert stored == hashlib.sha256(token.encode()).hexdigest()
        assert token.encode() not in database.read_bytes()
    service.reset_password(token, "new password")
    with pytest.raises(AuthenticationRequired):
        service.reset_password(token, "another password")
    with pytest.raises(AuthenticationRequired):
        service.authenticate(active.session_token)
    assert service.login("user@example.com", "new password", "127.0.0.1")


def test_auth_http_cookie_csrf_same_origin_and_secret_safe_responses(auth):
    service, database, _, _ = auth
    app, config = _app(service, database)
    origin = {"Origin": "http://testserver"}
    with TestClient(app) as client:
        missing_origin = client.post("/api/v1/auth/signup", json={"email": "user@example.com", "password": "password"})
        assert missing_origin.status_code == 403
        signup = client.post("/api/v1/auth/signup", headers=origin, json={"email": "user@example.com", "password": "password"})
        assert signup.status_code == 201
        cookie = signup.headers.get_list("set-cookie")
        assert any(f"{config.session.cookie_name}=" in item and "HttpOnly" in item and "SameSite=lax" in item and "Path=/" in item for item in cookie)
        assert all("password" not in item for item in cookie)
        me = client.get("/api/v1/users/me")
        assert me.status_code == 200
        assert "password" not in me.text and "token_hash" not in me.text
        no_csrf = client.patch("/api/v1/users/me", headers=origin, json={"display_name": "Changed"})
        assert no_csrf.status_code == 403
        wrong_origin = client.patch("/api/v1/users/me", headers={"Origin": "https://evil.example", "X-CSRF-Token": signup.json()["csrf_token"]}, json={"display_name": "Changed"})
        assert wrong_origin.status_code == 403
        changed = client.patch("/api/v1/users/me", headers={**origin, "X-CSRF-Token": signup.json()["csrf_token"]}, json={"display_name": "Changed"})
        assert changed.status_code == 200
        logout = client.post("/api/v1/auth/logout", headers={**origin, "X-CSRF-Token": signup.json()["csrf_token"]})
        assert logout.status_code == 204
        assert client.get("/api/v1/users/me").status_code == 401


def test_reset_request_http_response_is_identical_for_known_and_unknown(auth):
    service, database, _, _ = auth
    service.signup("user@example.com", "password")
    app, _ = _app(service, database)
    with TestClient(app) as client:
        known = client.post("/api/v1/auth/password-reset-requests", headers={"Origin": "http://testserver"}, json={"email": "user@example.com"})
        unknown = client.post("/api/v1/auth/password-reset-requests", headers={"Origin": "http://testserver"}, json={"email": "missing@example.com"})
    assert (known.status_code, known.json()) == (unknown.status_code, unknown.json())


def test_absolute_expiry_and_same_second_oldest_session_revocation(auth):
    service, database, clock, _ = auth
    first = service.signup("user@example.com", "password")
    tokens = [first]
    for _ in range(10):
        tokens.append(service.login("user@example.com", "password", "127.0.0.1"))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT revoked_at FROM sessions WHERE id=?", (first.session_id,)).fetchone()[0] is not None
    clock.now += timedelta(days=7)
    with pytest.raises(AuthenticationRequired):
        service.authenticate(tokens[-1].session_token)


def test_reset_provider_failure_remains_nondisclosing_and_audited(auth):
    service, database, _, _ = auth
    service.signup("user@example.com", "password")

    class FailingProvider:
        def send_password_reset(self, _message):
            raise RuntimeError("provider unavailable")

    service.notification_provider = FailingProvider()
    service.request_password_reset("missing@example.com")
    service.request_password_reset("user@example.com")
    with sqlite3.connect(database) as connection:
        actions = [row[0] for row in connection.execute("SELECT action FROM audit_logs")]
    assert "auth.password_reset.delivery_failed" in actions


def test_trusted_proxy_chain_uses_first_untrusted_hop_from_right():
    request = Request({"type": "http", "method": "POST", "path": "/", "headers": [(b"x-forwarded-for", b"198.51.100.66, 203.0.113.9")], "client": ("127.0.0.1", 1234), "server": ("test", 80), "scheme": "http", "query_string": b""})
    assert _client_ip(request, ("127.0.0.1/32", "203.0.113.0/24")) == "198.51.100.66"
    assert _client_ip(request, ("127.0.0.1/32",)) == "203.0.113.9"


def test_access_log_filter_redacts_reset_tokens():
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s %s", ("GET", "/reset-password?token=RESETSECRET"), None)
    AuthTokenRedactionFilter().filter(record)
    assert "RESETSECRET" not in record.getMessage()
    record = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "%s", ("/api/v1/auth/password-resets/RESETSECRET",), None)
    AuthTokenRedactionFilter().filter(record)
    assert "RESETSECRET" not in record.getMessage()


def test_local_outbox_forces_existing_file_to_owner_only(tmp_path):
    path = tmp_path / "outbox.jsonl"
    path.write_text("", encoding="utf-8")
    os.chmod(path, 0o644)
    LocalOutboxNotificationProvider(path).send_password_reset(PasswordResetMessage("user@example.com", "http://test/reset"))
    assert path.stat().st_mode & 0o777 == 0o600


def test_local_outbox_rejects_preexisting_symlink(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged", encoding="utf-8")
    outbox = tmp_path / "outbox.jsonl"
    outbox.symlink_to(victim)
    provider = LocalOutboxNotificationProvider(outbox)
    with pytest.raises(OSError):
        provider.send_password_reset(PasswordResetMessage("user@example.com", "http://test/reset?token=RESETSECRET"))
    assert victim.read_text(encoding="utf-8") == "unchanged"
