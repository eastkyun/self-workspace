"""Validated process configuration for the modular monolith.

This module is intentionally independent of FastAPI and the domain packages. It
defines the environment contract consumed by the Portal composition root; domain
factories still accept explicit injected paths for isolated tests.
"""

from __future__ import annotations

import os
import re
from ipaddress import ip_address, ip_network
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_CALENDAR_DB_PATH = WORKSPACE_ROOT / "instance" / "calendar" / "tasks.db"
DEFAULT_NOTES_DB_PATH = WORKSPACE_ROOT / "instance" / "notes" / "notes.db"
DEFAULT_PUBLISHING_DB_PATH = (
    WORKSPACE_ROOT / "instance" / "publishing" / "publishing.db"
)
DEFAULT_ASSET_DB_PATH = WORKSPACE_ROOT / "instance" / "assets" / "assets.db"
DEFAULT_IDENTITY_DB_PATH = WORKSPACE_ROOT / "instance" / "identity" / "identity.db"
DEFAULT_PUBLICATION_TYPES_PATH = (
    WORKSPACE_ROOT / "instance" / "publishing" / "types.json"
)

_ENVIRONMENTS = frozenset({"development", "test", "production"})
_COOKIE_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_DNS_LABEL = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")


class ConfigurationError(ValueError):
    """Raised when an explicitly supplied environment value is invalid."""


@dataclass(frozen=True, slots=True)
class DatabasePaths:
    calendar: Path
    notes: Path
    publishing: Path
    assets: Path
    identity: Path
    publication_types: Path


@dataclass(frozen=True, slots=True)
class SessionConfig:
    cookie_name: str
    cookie_secure: bool
    idle_ttl_seconds: int
    absolute_ttl_seconds: int
    max_active_per_user: int
    last_seen_interval_seconds: int


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    invitation_ttl_seconds: int
    password_reset_ttl_seconds: int
    login_window_seconds: int
    login_max_failures: int
    trusted_proxies: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocumentExportConfig:
    pandoc_path: str
    xelatex_path: str
    chromium_path: str
    pdf_engine: str
    cjk_font: str
    timeout_seconds: int
    max_concurrent: int
    reference_docx: Path | None



@dataclass(frozen=True, slots=True)
class AppConfig:
    environment: str
    public_origin: str | None
    databases: DatabasePaths
    session: SessionConfig
    security: SecurityConfig
    document_export: DocumentExportConfig


def _value(source: Mapping[str, str], name: str) -> str | None:
    value = source.get(name)
    if value is None:
        return None
    value = value.strip()
    if not value:
        raise ConfigurationError(f"{name} must not be empty")
    if "\x00" in value:
        raise ConfigurationError(f"{name} must not contain a NUL byte")
    return value


def _path(
    source: Mapping[str, str],
    name: str,
    default: Path,
    *,
    relative_to_workspace: bool = False,
) -> Path:
    configured = _value(source, name)
    if configured is None:
        return default
    path = Path(configured).expanduser()
    if relative_to_workspace and not path.is_absolute():
        return WORKSPACE_ROOT / path
    return path.resolve()


def _positive_int(source: Mapping[str, str], name: str, default: int) -> int:
    configured = _value(source, name)
    if configured is None:
        return default
    try:
        value = int(configured)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive integer")
    return value


def _boolean(source: Mapping[str, str], name: str, default: bool) -> bool:
    configured = _value(source, name)
    if configured is None:
        return default
    normalized = configured.casefold()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigurationError(
        f"{name} must be one of: 1, 0, true, false, yes, no, on, off"
    )


def load_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Parse and validate configuration from ``environ`` or ``os.environ``.

    Missing values use the existing local-development paths and the security
    defaults fixed by the workspace plan.  An explicitly present but empty or
    malformed value fails closed rather than silently falling back.
    """

    source = os.environ if environ is None else environ
    environment = (_value(source, "APP_ENV") or "development").casefold()
    if environment not in _ENVIRONMENTS:
        allowed = ", ".join(sorted(_ENVIRONMENTS))
        raise ConfigurationError(f"APP_ENV must be one of: {allowed}")

    public_origin = _value(source, "PUBLIC_ORIGIN")
    if public_origin is not None:
        if any(character.isspace() or ord(character) < 32 for character in public_origin):
            raise ConfigurationError("PUBLIC_ORIGIN contains invalid characters")
        if "\\" in public_origin:
            raise ConfigurationError("PUBLIC_ORIGIN contains invalid characters")
        parsed_origin = urlsplit(public_origin)
        try:
            parsed_port = parsed_origin.port
        except ValueError as exc:
            raise ConfigurationError("PUBLIC_ORIGIN has an invalid port") from exc
        hostname = parsed_origin.hostname
        valid_hostname = False
        if hostname:
            try:
                ip_address(hostname)
                valid_hostname = True
            except ValueError:
                labels = hostname.rstrip(".").split(".")
                valid_hostname = bool(labels) and all(
                    _DNS_LABEL.fullmatch(label) for label in labels
                )
        if (
            parsed_origin.scheme not in {"http", "https"}
            or not parsed_origin.netloc
            or not valid_hostname
            or parsed_origin.username is not None
            or parsed_origin.password is not None
            or parsed_origin.path not in {"", "/"}
            or parsed_origin.query
            or parsed_origin.fragment
        ):
            raise ConfigurationError("PUBLIC_ORIGIN must be an HTTP(S) origin")
        if environment == "production" and parsed_origin.scheme != "https":
            raise ConfigurationError("PUBLIC_ORIGIN must use HTTPS in production")
        port_suffix = f":{parsed_port}" if parsed_port is not None else ""
        bracketed_host = f"[{hostname}]" if hostname and ":" in hostname else hostname
        public_origin = f"{parsed_origin.scheme}://{bracketed_host}{port_suffix}"
    elif environment == "production":
        raise ConfigurationError("PUBLIC_ORIGIN is required in production")

    calendar_name = "CALENDAR_DB_PATH"
    if calendar_name not in source and "TODO_DB_PATH" in source:
        calendar_name = "TODO_DB_PATH"

    databases = DatabasePaths(
        calendar=_path(
            source,
            calendar_name,
            DEFAULT_CALENDAR_DB_PATH,
            relative_to_workspace=True,
        ),
        notes=_path(source, "NOTES_DB_PATH", DEFAULT_NOTES_DB_PATH),
        publishing=_path(
            source, "PUBLISHING_DB_PATH", DEFAULT_PUBLISHING_DB_PATH
        ),
        assets=_path(source, "ASSET_DB_PATH", DEFAULT_ASSET_DB_PATH),
        identity=_path(source, "IDENTITY_DB_PATH", DEFAULT_IDENTITY_DB_PATH),
        publication_types=_path(
            source, "PUBLICATION_TYPES_PATH", DEFAULT_PUBLICATION_TYPES_PATH
        ),
    )

    cookie_name = _value(source, "SESSION_COOKIE_NAME") or "my_workspace_session"
    if not _COOKIE_NAME.fullmatch(cookie_name):
        raise ConfigurationError(
            "SESSION_COOKIE_NAME may contain only letters, digits, '_' and '-'"
        )
    idle_ttl = _positive_int(source, "SESSION_IDLE_TTL_SECONDS", 8 * 60 * 60)
    absolute_ttl = _positive_int(
        source, "SESSION_ABSOLUTE_TTL_SECONDS", 7 * 24 * 60 * 60
    )
    if absolute_ttl < idle_ttl:
        raise ConfigurationError(
            "SESSION_ABSOLUTE_TTL_SECONDS must be greater than or equal to "
            "SESSION_IDLE_TTL_SECONDS"
        )

    cookie_secure = _boolean(
        source, "SESSION_COOKIE_SECURE", environment == "production"
    )
    if environment == "production" and not cookie_secure:
        raise ConfigurationError(
            "SESSION_COOKIE_SECURE must be true when APP_ENV is production"
        )

    session = SessionConfig(
        cookie_name=cookie_name,
        cookie_secure=cookie_secure,
        idle_ttl_seconds=idle_ttl,
        absolute_ttl_seconds=absolute_ttl,
        max_active_per_user=_positive_int(source, "SESSION_MAX_ACTIVE_PER_USER", 10),
        last_seen_interval_seconds=_positive_int(
            source, "SESSION_LAST_SEEN_INTERVAL_SECONDS", 5 * 60
        ),
    )

    proxies = _value(source, "TRUSTED_PROXIES")
    trusted_proxies = (
        tuple(part.strip() for part in proxies.split(",") if part.strip())
        if proxies is not None
        else ()
    )
    if proxies is not None and not trusted_proxies:
        raise ConfigurationError("TRUSTED_PROXIES must contain at least one value")
    for proxy in trusted_proxies:
        try:
            ip_network(proxy, strict=False)
        except ValueError as exc:
            raise ConfigurationError(
                "TRUSTED_PROXIES must contain only IP addresses or CIDR networks"
            ) from exc

    security = SecurityConfig(
        invitation_ttl_seconds=_positive_int(
            source, "INVITATION_TTL_SECONDS", 24 * 60 * 60
        ),
        password_reset_ttl_seconds=_positive_int(
            source, "PASSWORD_RESET_TTL_SECONDS", 30 * 60
        ),
        login_window_seconds=_positive_int(
            source, "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 15 * 60
        ),
        login_max_failures=_positive_int(source, "LOGIN_RATE_LIMIT_MAX_FAILURES", 5),
        trusted_proxies=trusted_proxies,
    )
    reference_docx_value = _value(source, "DOCUMENT_EXPORT_REFERENCE_DOCX")
    document_export = DocumentExportConfig(
        pandoc_path=_value(source, "DOCUMENT_EXPORT_PANDOC_PATH") or "pandoc",
        xelatex_path=_value(source, "DOCUMENT_EXPORT_XELATEX_PATH") or "xelatex",
        cjk_font=_value(source, "DOCUMENT_EXPORT_CJK_FONT") or "Noto Sans CJK KR",
        chromium_path=_value(source, "DOCUMENT_EXPORT_CHROMIUM_PATH") or "chromium",
        pdf_engine=(_value(source, "DOCUMENT_EXPORT_PDF_ENGINE") or "chromium").casefold(),
        timeout_seconds=_positive_int(source, "DOCUMENT_EXPORT_TIMEOUT_SECONDS", 60),
        max_concurrent=_positive_int(source, "DOCUMENT_EXPORT_MAX_CONCURRENT", 2),
        reference_docx=(
            Path(reference_docx_value).expanduser().resolve()
            if reference_docx_value is not None
            else None
        ),
    )
    if document_export.pdf_engine not in {"chromium", "xelatex"}:
        raise ConfigurationError(
            "DOCUMENT_EXPORT_PDF_ENGINE must be chromium or xelatex"
        )
    return AppConfig(
        environment=environment,
        public_origin=public_origin,
        databases=databases,
        session=session,
        security=security,
        document_export=document_export,
    )


__all__ = [
    "AppConfig",
    "ConfigurationError",
    "DatabasePaths",
    "DocumentExportConfig",
    "SecurityConfig",
    "SessionConfig",
    "load_config",
]
