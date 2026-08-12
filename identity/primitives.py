"""Canonical values shared by the identity domain."""

from __future__ import annotations

import unicodedata
from datetime import datetime, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def new_uuid() -> str:
    """Return a canonical random UUID string."""

    return str(uuid4())


def validate_uuid(value: str) -> str:
    """Validate and return the canonical lowercase UUID representation."""

    if not isinstance(value, str):
        raise ValueError("UUID must be a string")
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError("invalid UUID") from error
    canonical = str(parsed)
    if value != canonical:
        raise ValueError("UUID must use canonical lowercase hyphenated form")
    return canonical


def utc_now() -> datetime:
    """Return an aware current time in UTC."""

    return datetime.now(timezone.utc)


def normalize_email(value: str) -> str:
    """Trim, Unicode-normalize, and lowercase an identity email key."""

    if not isinstance(value, str):
        raise ValueError("email must be a string")
    normalized = unicodedata.normalize("NFKC", value).strip().lower()
    if not normalized:
        raise ValueError("email must not be empty")
    return normalized


def validate_timezone(value: str) -> str:
    """Validate an IANA timezone name and return it unchanged."""

    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("timezone must be a non-empty IANA name")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("unknown IANA timezone") from error
    return value
