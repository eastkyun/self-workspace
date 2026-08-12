"""Secret-safe filters for framework access logs."""

from __future__ import annotations

import logging
import re


_RESET_QUERY = re.compile(r"([?&]token=)[^&\s]+", re.IGNORECASE)
_RESET_PATH = re.compile(r"(/api/v1/auth/password-resets/)[^/?\s]+", re.IGNORECASE)
_INVITATION_PATH = re.compile(
    r"((?:/api/v1)?/invitations/)[^/?\s]+", re.IGNORECASE
)


def redact_auth_tokens(value: str) -> str:
    value = _RESET_QUERY.sub(r"\1[REDACTED]", value)
    value = _RESET_PATH.sub(r"\1[REDACTED]", value)
    return _INVITATION_PATH.sub(r"\1[REDACTED]", value)


class AuthTokenRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact_auth_tokens(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact_auth_tokens(value) if isinstance(value, str) else value
                for value in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: redact_auth_tokens(value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        return True


def install_access_log_redaction() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, AuthTokenRedactionFilter) for item in logger.filters):
        logger.addFilter(AuthTokenRedactionFilter())
