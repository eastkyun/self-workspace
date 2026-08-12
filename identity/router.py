"""Versioned HTTP and HTML authentication routes."""

from __future__ import annotations

from ipaddress import ip_address, ip_network
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Header, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict

from identity.auth import (
    AuthenticatedUser,
    AuthenticationConflict,
    AuthenticationForbidden,
    AuthenticationRateLimited,
    AuthenticationRequired,
    AuthenticationService,
    SessionCredentials,
)
from shared.config import AppConfig
from shared.errors import APIError
from shared.templating import create_templates


TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = create_templates(TEMPLATES_DIR)


class SignupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str
    display_name: str | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str


class ProfileUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    disable: bool = False


class PasswordChangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str
    new_password: str


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str


class PasswordResetCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    new_password: str


def _origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    default = 80 if parsed.scheme == "http" else 443
    suffix = "" if port in {None, default} else f":{port}"
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    return f"{parsed.scheme}://{host}{suffix}"


def _require_same_origin(request: Request, expected_origin: str | None) -> None:
    supplied = request.headers.get("origin")
    if supplied is None:
        supplied = request.headers.get("referer")
    actual_expected = expected_origin or str(request.base_url)
    if supplied is None or _origin(supplied) != _origin(actual_expected):
        raise APIError(status_code=403, code="csrf_failed", message="CSRF validation failed")


def _client_ip(request: Request, trusted_proxies: tuple[str, ...]) -> str:
    direct = request.client.host if request.client else "unknown"
    try:
        direct_ip = ip_address(direct)
    except ValueError:
        return direct
    trusted = tuple(ip_network(value, strict=False) for value in trusted_proxies)
    if any(direct_ip in network for network in trusted):
        try:
            chain = [
                ip_address(value.strip())
                for value in request.headers.get("x-forwarded-for", "").split(",")
                if value.strip()
            ]
        except ValueError:
            return str(direct_ip)
        # Append-style proxies add hops on the right. Walk from the immediate
        # peer backwards and discard only explicitly trusted proxy hops.
        for hop in reversed((*chain, direct_ip)):
            if any(hop in network for network in trusted):
                continue
            return str(hop)
    return str(direct_ip)


def _set_session_cookies(response: Response, credentials: SessionCredentials, config: AppConfig) -> None:
    common = {
        "secure": config.session.cookie_secure,
        "samesite": "lax",
        "path": "/",
        "max_age": config.session.absolute_ttl_seconds,
    }
    response.set_cookie(config.session.cookie_name, credentials.session_token, httponly=True, **common)
    response.set_cookie(f"{config.session.cookie_name}_csrf", credentials.csrf_token, httponly=False, **common)


def _clear_session_cookies(response: Response, config: AppConfig) -> None:
    response.delete_cookie(config.session.cookie_name, path="/", secure=config.session.cookie_secure, samesite="lax", httponly=True)
    response.delete_cookie(f"{config.session.cookie_name}_csrf", path="/", secure=config.session.cookie_secure, samesite="lax", httponly=False)


def _auth_error(error: Exception) -> APIError:
    if isinstance(error, AuthenticationRateLimited):
        return APIError(status_code=429, code="authentication_rate_limited", message="Authentication temporarily unavailable")
    if isinstance(error, AuthenticationConflict):
        return APIError(status_code=409, code="authentication_conflict", message=str(error))
    if isinstance(error, AuthenticationForbidden):
        return APIError(status_code=403, code="csrf_failed", message="CSRF validation failed")
    return APIError(status_code=401, code="authentication_required", message="Authentication required")


def create_auth_router(service: AuthenticationService, config: AppConfig) -> APIRouter:
    router = APIRouter()
    expected_origin = config.public_origin
    cookie_name = config.session.cookie_name

    def safe_return_path(value: str | None) -> str:
        if not value or not value.startswith("/") or value.startswith("//"):
            return "/workspace-selector"
        if "\\" in value or any(ord(character) < 32 for character in value):
            return "/workspace-selector"
        return value

    def authenticate(request: Request) -> AuthenticatedUser:
        try:
            return service.authenticate(request.cookies.get(cookie_name))
        except AuthenticationRequired as error:
            raise _auth_error(error) from error

    def csrf(request: Request, user: AuthenticatedUser) -> None:
        _require_same_origin(request, expected_origin)
        try:
            service.validate_csrf(user.session_id, request.headers.get("x-csrf-token"))
        except AuthenticationForbidden as error:
            raise _auth_error(error) from error

    @router.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_page(request: Request, next: str | None = None):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"page_title": "Log in", "login_success_url": safe_return_path(next)},
        )

    @router.get("/signup", response_class=HTMLResponse, include_in_schema=False)
    def signup_page(request: Request):
        return templates.TemplateResponse(request, "signup.html", {"page_title": "Sign up"})

    @router.get("/forgot-password", response_class=HTMLResponse, include_in_schema=False)
    def forgot_password_page(request: Request):
        return templates.TemplateResponse(request, "forgot_password.html", {"page_title": "Reset password"})

    @router.get("/reset-password", response_class=HTMLResponse, include_in_schema=False)
    def reset_password_page(request: Request):
        return templates.TemplateResponse(request, "reset_password.html", {"page_title": "Choose a new password"})

    @router.post("/api/v1/auth/signup", status_code=201)
    def signup(payload: SignupRequest, request: Request, response: Response):
        _require_same_origin(request, expected_origin)
        try:
            credentials = service.signup(payload.email, payload.password, payload.display_name)
        except (AuthenticationConflict, ValueError) as error:
            raise _auth_error(AuthenticationConflict("account cannot be created")) from error
        _set_session_cookies(response, credentials, config)
        return {"user_id": credentials.user_id, "workspace_id": credentials.workspace_id, "csrf_token": credentials.csrf_token}

    @router.post("/api/v1/auth/login")
    def login(payload: LoginRequest, request: Request, response: Response):
        _require_same_origin(request, expected_origin)
        try:
            credentials = service.login(
                payload.email,
                payload.password,
                _client_ip(request, config.security.trusted_proxies),
                request.cookies.get(cookie_name),
            )
        except (AuthenticationRequired, AuthenticationRateLimited) as error:
            raise _auth_error(error) from error
        _set_session_cookies(response, credentials, config)
        return {"user_id": credentials.user_id, "workspace_id": credentials.workspace_id, "csrf_token": credentials.csrf_token}

    @router.post("/api/v1/auth/logout", status_code=204)
    def logout(request: Request, response: Response):
        user = authenticate(request)
        csrf(request, user)
        service.logout(user)
        _clear_session_cookies(response, config)

    @router.get("/api/v1/users/me")
    def me(request: Request):
        user = authenticate(request)
        return {"id": user.id, "email": user.email, "display_name": user.display_name, "last_workspace_id": user.last_workspace_id}

    @router.patch("/api/v1/users/me")
    def update_me(payload: ProfileUpdateRequest, request: Request, response: Response):
        user = authenticate(request)
        csrf(request, user)
        try:
            updated = service.update_profile(user, display_name=payload.display_name, disable=payload.disable)
        except AuthenticationConflict as error:
            raise _auth_error(error) from error
        if payload.disable:
            _clear_session_cookies(response, config)
            return {"disabled": True}
        return {"id": updated.id, "email": updated.email, "display_name": updated.display_name, "last_workspace_id": updated.last_workspace_id}

    @router.patch("/api/v1/users/me/password", status_code=204)
    def change_password(payload: PasswordChangeRequest, request: Request, response: Response):
        user = authenticate(request)
        csrf(request, user)
        try:
            service.change_password(user, payload.current_password, payload.new_password)
        except (AuthenticationRequired, AuthenticationConflict) as error:
            raise _auth_error(error) from error
        _clear_session_cookies(response, config)

    @router.post("/api/v1/auth/password-reset-requests", status_code=202)
    def request_reset(payload: PasswordResetRequest, request: Request):
        _require_same_origin(request, expected_origin)
        service.request_password_reset(payload.email)
        return {"message": "If the account exists, reset instructions were sent."}

    @router.post("/api/v1/auth/password-resets/{token}", status_code=204)
    def complete_reset(token: str, payload: PasswordResetCompletion, request: Request):
        _require_same_origin(request, expected_origin)
        try:
            service.reset_password(token, payload.new_password)
        except AuthenticationRequired as error:
            raise _auth_error(error) from error

    return router
