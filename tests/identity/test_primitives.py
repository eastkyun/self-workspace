from __future__ import annotations

from datetime import timezone
from uuid import UUID

import pytest

from identity.primitives import new_uuid, normalize_email, utc_now, validate_timezone, validate_uuid


def test_uuid_generation_and_validation_are_canonical():
    value = new_uuid()
    assert str(UUID(value)) == value
    assert validate_uuid(value) == value
    for invalid in (value.upper(), value.replace("-", ""), "not-a-uuid"):
        with pytest.raises(ValueError):
            validate_uuid(invalid)


def test_utc_now_is_timezone_aware_utc():
    value = utc_now()
    assert value.tzinfo is timezone.utc
    assert value.utcoffset().total_seconds() == 0


def test_email_is_trimmed_nfkc_normalized_and_lowercased():
    assert normalize_email("  ＴＥＳＴ@Example.COM  ") == "test@example.com"
    with pytest.raises(ValueError):
        normalize_email("  ")


def test_timezone_requires_an_iana_zone_name():
    assert validate_timezone("Asia/Seoul") == "Asia/Seoul"
    with pytest.raises(ValueError):
        validate_timezone("Not/A_Zone")
