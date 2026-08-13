from __future__ import annotations

import re
from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse, RedirectResponse, Response


REQUEST_ID_HEADER = "X-Request-ID"
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_STATUS_CODES = {
    400: "bad_request",
    401: "authentication_required",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}
_SAFE_DETAIL_KEYS = frozenset(
    {
        "app_key",
        "field",
        "loc",
        "membership_id",
        "permission_code",
        "reason",
        "resource_id",
        "type",
        "workspace_id",
    }
)
_AUTHENTICATED_UI_PATH = re.compile(
r"^(?:/w/[^/]+/(?:notes(?:/new)?|calendar|publications|home|search|settings/(?:profile|general|members|apps|roles|permissions))|/apps/publishing)/?$"
)


class ErrorBody(BaseModel):
    code: str
    message: str
    request_id: str
    details: Any | None = None


class APIError(Exception):
    """An expected API failure with a stable, client-facing error code."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        details: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def _request_id(request: Request) -> str:
    state_request_id = getattr(request.state, "request_id", None)
    if isinstance(state_request_id, str) and _REQUEST_ID_PATTERN.fullmatch(
        state_request_id
    ):
        return state_request_id

    supplied_request_id = request.headers.get(REQUEST_ID_HEADER)
    if supplied_request_id and _REQUEST_ID_PATTERN.fullmatch(supplied_request_id):
        return supplied_request_id
    return uuid4().hex


def _response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Any | None = None,
    headers: dict[str, str] | None = None,
    allow_list_details: bool = False,
) -> JSONResponse:
    request_id = _request_id(request)
    response_headers = dict(headers or {})
    response_headers[REQUEST_ID_HEADER] = request_id
    body = ErrorBody(
        code=code,
        message=message,
        request_id=request_id,
        details=_sanitize_details(details, allow_list_root=allow_list_details),
    )
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(body.model_dump(exclude_none=True)),
        headers=response_headers,
    )


async def api_error_handler(request: Request, error: APIError) -> Response:
    if (
        error.status_code == 401
        and request.method == "GET"
        and _AUTHENTICATED_UI_PATH.fullmatch(request.url.path)
    ):
        target = request.url.path
        if request.url.query:
            target += f"?{request.url.query}"
        return RedirectResponse(
            f"/login?next={quote(target, safe='')}", status_code=307
        )
    return _response(
        request,
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        details=error.details,
        headers=error.headers,
    )


async def http_exception_handler(
    request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    code = _STATUS_CODES.get(error.status_code, "http_error")
    details: Any | None = None
    if isinstance(error.detail, dict):
        detail = dict(error.detail)
        detail_code = detail.pop("code", None)
        detail_message = detail.pop("message", None)
        if isinstance(detail_code, str) and detail_code:
            code = detail_code
        message = (
            detail_message
            if isinstance(detail_message, str) and detail_message
            else _status_message(error.status_code)
        )
        details = detail or None
    elif isinstance(error.detail, str):
        message = error.detail
    else:
        message = _status_message(error.status_code)
        details = error.detail
    return _response(
        request,
        status_code=error.status_code,
        code=code,
        message=message,
        details=details,
        headers=error.headers,
    )


async def validation_exception_handler(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    return _response(
        request,
        status_code=422,
        code="validation_error",
        message="Request validation failed",
        details=[
            {
                "type": item.get("type"),
                "loc": item.get("loc"),
            }
            for item in error.errors()
        ],
        allow_list_details=True,
    )


async def unhandled_exception_handler(
    request: Request,
    _: Exception,
) -> JSONResponse:
    return _response(
        request,
        status_code=500,
        code="internal_server_error",
        message="An unexpected error occurred",
    )


def install_exception_handlers(application: FastAPI) -> None:
    """Install the shared error contract without changing success responses."""

    application.add_exception_handler(APIError, api_error_handler)
    application.add_exception_handler(
        StarletteHTTPException,
        http_exception_handler,
    )
    application.add_exception_handler(HTTPException, http_exception_handler)
    application.add_exception_handler(
        RequestValidationError,
        validation_exception_handler,
    )
    application.add_exception_handler(Exception, unhandled_exception_handler)


def _status_message(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Request failed"


def _sanitize_details(
    value: Any,
    *,
    depth: int = 0,
    allow_list_root: bool = False,
) -> Any | None:
    """Return bounded, JSON-safe error metadata without request secrets."""

    if value is None or depth > 4:
        return None
    if depth == 0 and not isinstance(value, dict):
        if not (allow_list_root and isinstance(value, (list, tuple))):
            return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if key.casefold() not in _SAFE_DETAIL_KEYS:
                continue
            clean = _sanitize_details(item, depth=depth + 1)
            if clean is not None:
                sanitized[key] = clean
        return sanitized or None
    if isinstance(value, (list, tuple)):
        sanitized_items = [
            clean
            for item in value[:50]
            if (clean := _sanitize_details(item, depth=depth + 1)) is not None
        ]
        return sanitized_items or None
    return None
