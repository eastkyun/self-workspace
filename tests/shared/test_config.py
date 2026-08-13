from pathlib import Path

import pytest

from shared.config import (
    DEFAULT_ASSET_DB_PATH,
    DEFAULT_CALENDAR_DB_PATH,
    DEFAULT_IDENTITY_DB_PATH,
    DEFAULT_NOTES_DB_PATH,
    DEFAULT_PUBLICATION_TYPES_PATH,
    DEFAULT_PUBLISHING_DB_PATH,
    WORKSPACE_ROOT,
    ConfigurationError,
    load_config,
)
from portal.main import create_app


def test_missing_environment_uses_existing_paths_and_security_defaults() -> None:
    config = load_config({})

    assert config.environment == "development"
    assert config.databases.calendar == DEFAULT_CALENDAR_DB_PATH
    assert config.databases.notes == DEFAULT_NOTES_DB_PATH
    assert config.databases.publishing == DEFAULT_PUBLISHING_DB_PATH
    assert config.databases.assets == DEFAULT_ASSET_DB_PATH
    assert config.databases.identity == DEFAULT_IDENTITY_DB_PATH
    assert config.databases.publication_types == DEFAULT_PUBLICATION_TYPES_PATH
    assert config.session.idle_ttl_seconds == 28_800
    assert config.session.absolute_ttl_seconds == 604_800
    assert config.session.cookie_secure is False
    assert config.security.trusted_proxies == ()
    assert config.document_export.pandoc_path == "pandoc"
    assert config.document_export.xelatex_path == "xelatex"
    assert config.document_export.chromium_path == "chromium"
    assert config.document_export.pdf_engine == "chromium"
    assert config.document_export.cjk_font == "Noto Sans CJK KR"
    assert config.document_export.timeout_seconds == 60
    assert config.document_export.max_concurrent == 2
    assert config.document_export.reference_docx is None
    assert config.databases.jobs.name == "jobs.db"
    assert config.jobs.enabled is False
    assert config.jobs.web_collection_enabled is False
    assert config.jobs.minimum_interval_seconds == 300
    assert config.jobs.lease_seconds == 60
    assert config.jobs.heartbeat_seconds == 20


def test_current_database_environment_names_and_calendar_legacy_fallback(
    tmp_path: Path,
) -> None:
    config = load_config(
        {
            "TODO_DB_PATH": "instance/calendar/legacy.db",
            "NOTES_DB_PATH": str(tmp_path / "notes.db"),
            "PUBLISHING_DB_PATH": str(tmp_path / "publishing.db"),
            "ASSET_DB_PATH": str(tmp_path / "assets.db"),
            "IDENTITY_DB_PATH": str(tmp_path / "identity.db"),
            "PUBLICATION_TYPES_PATH": str(tmp_path / "types.json"),
        }
    )

    assert config.databases.calendar == WORKSPACE_ROOT / "instance/calendar/legacy.db"
    assert config.databases.notes == (tmp_path / "notes.db").resolve()
    assert config.databases.publishing == (tmp_path / "publishing.db").resolve()
    assert config.databases.assets == (tmp_path / "assets.db").resolve()
    assert config.databases.identity == (tmp_path / "identity.db").resolve()
    assert config.databases.publication_types == (tmp_path / "types.json").resolve()


def test_calendar_environment_name_takes_precedence_over_legacy_name() -> None:
    config = load_config(
        {
            "CALENDAR_DB_PATH": "instance/calendar/current.db",
            "TODO_DB_PATH": "instance/calendar/legacy.db",
        }
    )

    assert config.databases.calendar == WORKSPACE_ROOT / "instance/calendar/current.db"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("APP_ENV", "staging"),
        ("NOTES_DB_PATH", "  "),
        ("SESSION_IDLE_TTL_SECONDS", "eight-hours"),
        ("SESSION_MAX_ACTIVE_PER_USER", "0"),
        ("SESSION_COOKIE_SECURE", "sometimes"),
        ("SESSION_COOKIE_NAME", "bad cookie"),
        ("DOCUMENT_EXPORT_TIMEOUT_SECONDS", "0"),
        ("DOCUMENT_EXPORT_MAX_CONCURRENT", "many"),
        ("DOCUMENT_EXPORT_PDF_ENGINE", "printer"),
    ],
)
def test_invalid_environment_values_fail_closed(name: str, value: str) -> None:
    with pytest.raises(ConfigurationError, match=name):
        load_config({name: value})


def test_absolute_session_ttl_cannot_be_shorter_than_idle_ttl() -> None:
    with pytest.raises(ConfigurationError, match="SESSION_ABSOLUTE_TTL_SECONDS"):
        load_config(
            {
                "SESSION_IDLE_TTL_SECONDS": "3600",
                "SESSION_ABSOLUTE_TTL_SECONDS": "3599",
            }
        )


def test_document_export_environment_is_validated(tmp_path: Path) -> None:
    reference = tmp_path / "reference.docx"
    config = load_config(
        {
            "DOCUMENT_EXPORT_PANDOC_PATH": "/opt/pandoc",
            "DOCUMENT_EXPORT_XELATEX_PATH": "/opt/xelatex",
            "DOCUMENT_EXPORT_CHROMIUM_PATH": "/opt/chromium",
            "DOCUMENT_EXPORT_PDF_ENGINE": "xelatex",
            "DOCUMENT_EXPORT_CJK_FONT": "Noto Serif CJK KR",
            "DOCUMENT_EXPORT_TIMEOUT_SECONDS": "45",
            "DOCUMENT_EXPORT_MAX_CONCURRENT": "3",
            "DOCUMENT_EXPORT_REFERENCE_DOCX": str(reference),
        }
    )

    assert config.document_export.pandoc_path == "/opt/pandoc"
    assert config.document_export.xelatex_path == "/opt/xelatex"
    assert config.document_export.chromium_path == "/opt/chromium"
    assert config.document_export.pdf_engine == "xelatex"
    assert config.document_export.cjk_font == "Noto Serif CJK KR"
    assert config.document_export.timeout_seconds == 45
    assert config.document_export.max_concurrent == 3
    assert config.document_export.reference_docx == reference.resolve()


def test_jobs_environment_is_fail_closed_and_validated(tmp_path: Path) -> None:
    config = load_config(
        {
            "JOBS_DB_PATH": str(tmp_path / "jobs.db"),
            "JOBS_ENABLED": "true",
            "JOBS_WEB_COLLECTION_ENABLED": "false",
            "JOBS_MINIMUM_INTERVAL_SECONDS": "600",
            "JOBS_LEASE_SECONDS": "90",
            "JOBS_HEARTBEAT_SECONDS": "30",
            "JOBS_BROWSER_CONCURRENCY": "1",
        }
    )
    assert config.databases.jobs == (tmp_path / "jobs.db").resolve()
    assert config.jobs.enabled is True
    assert config.jobs.web_collection_enabled is False
    assert config.jobs.minimum_interval_seconds == 600
    with pytest.raises(ConfigurationError, match="at least 300"):
        load_config({"JOBS_MINIMUM_INTERVAL_SECONDS": "299"})
    with pytest.raises(ConfigurationError, match="shorter"):
        load_config({"JOBS_LEASE_SECONDS": "20", "JOBS_HEARTBEAT_SECONDS": "20"})


def test_production_defaults_to_secure_cookie_and_rejects_insecure_override() -> None:
    assert load_config(
        {"APP_ENV": "production", "PUBLIC_ORIGIN": "https://workspace.example"}
    ).session.cookie_secure is True

    with pytest.raises(ConfigurationError, match="SESSION_COOKIE_SECURE"):
        load_config(
            {
                "APP_ENV": "production",
                "PUBLIC_ORIGIN": "https://workspace.example",
                "SESSION_COOKIE_SECURE": "false",
            }
        )


def test_production_requires_canonical_https_origin() -> None:
    with pytest.raises(ConfigurationError, match="PUBLIC_ORIGIN"):
        load_config({"APP_ENV": "production"})
    with pytest.raises(ConfigurationError, match="HTTPS"):
        load_config(
            {"APP_ENV": "production", "PUBLIC_ORIGIN": "http://workspace.example"}
        )


@pytest.mark.parametrize(
    "origin",
    (
        "https://:443",
        "https://example.com:bad",
        "https://exa mple.com",
        "https://example.com\\evil",
    ),
)
def test_public_origin_rejects_malformed_non_origins(origin: str) -> None:
    with pytest.raises(ConfigurationError, match="PUBLIC_ORIGIN"):
        load_config({"APP_ENV": "production", "PUBLIC_ORIGIN": origin})


def test_trusted_proxies_accept_only_ip_addresses_or_networks() -> None:
    config = load_config({"TRUSTED_PROXIES": "127.0.0.1,10.0.0.0/8,::1"})
    assert config.security.trusted_proxies == (
        "127.0.0.1",
        "10.0.0.0/8",
        "::1",
    )
    with pytest.raises(ConfigurationError, match="TRUSTED_PROXIES"):
        load_config({"TRUSTED_PROXIES": "proxy.example"})


def test_portal_startup_uses_the_shared_configuration_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://workspace.example")

    with pytest.raises(ConfigurationError, match="SESSION_COOKIE_SECURE"):
        create_app()
