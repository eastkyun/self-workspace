import asyncio
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError

from portal.main import create_app
from shared.errors import APIError, install_exception_handlers


async def get(
    application: FastAPI,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    raise_app_exceptions: bool = True,
) -> httpx.Response:
    transport = httpx.ASGITransport(
        app=application,
        raise_app_exceptions=raise_app_exceptions,
    )
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
    ) as client:
        return await client.get(path, headers=headers)


def test_http_errors_use_the_common_contract_and_preserve_headers() -> None:
    application = FastAPI()
    install_exception_handlers(application)

    @application.get("/limited")
    async def limited() -> None:
        raise HTTPException(
            status_code=429,
            detail="Try again later",
            headers={"Retry-After": "30"},
        )

    @application.get("/success")
    async def success() -> list[dict[str, str]]:
        return [{"status": "unchanged"}]

    response = asyncio.run(
        get(
            application,
            "/limited",
            headers={"X-Request-ID": "request-123"},
        )
    )
    success = asyncio.run(get(application, "/success"))

    assert success.json() == [{"status": "unchanged"}]
    assert response.status_code == 429
    assert response.json() == {
        "code": "rate_limited",
        "message": "Try again later",
        "request_id": "request-123",
    }
    assert response.headers["x-request-id"] == "request-123"
    assert response.headers["retry-after"] == "30"


def test_api_error_supports_stable_code_and_optional_details() -> None:
    application = FastAPI()
    install_exception_handlers(application)

    @application.get("/owner")
    async def remove_owner() -> None:
        raise APIError(
            status_code=409,
            code="last_owner_required",
            message="The last owner cannot be removed",
            details={"workspace_id": "workspace-1"},
        )

    response = asyncio.run(get(application, "/owner"))

    payload = response.json()
    assert response.status_code == 409
    assert payload["code"] == "last_owner_required"
    assert payload["message"] == "The last owner cannot be removed"
    assert payload["details"] == {"workspace_id": "workspace-1"}
    assert payload["request_id"] == response.headers["x-request-id"]
    assert len(payload["request_id"]) == 32


def test_authenticated_ui_paths_redirect_to_login_but_api_paths_stay_json() -> None:
    application = FastAPI()
    install_exception_handlers(application)

    @application.get("/{path:path}")
    async def protected(path: str) -> None:
        raise APIError(
            status_code=401,
            code="authentication_required",
            message="Authentication required",
        )

    publishing = asyncio.run(get(application, "/apps/publishing/"))
    canonical = asyncio.run(get(application, "/w/workspace-id/publications"))
    permissions = asyncio.run(get(application, "/w/workspace-id/settings/permissions"))
    api = asyncio.run(get(application, "/api/v1/workspaces/workspace-id/publications"))

    assert publishing.status_code == 307
    assert publishing.headers["location"] == "/login?next=%2Fapps%2Fpublishing%2F"
    assert canonical.status_code == 307
    assert canonical.headers["location"] == "/login?next=%2Fw%2Fworkspace-id%2Fpublications"
    assert permissions.status_code == 307
    assert api.status_code == 401
    assert api.json()["code"] == "authentication_required"


def test_validation_and_unhandled_errors_are_normalized() -> None:
    application = FastAPI()
    install_exception_handlers(application)

    @application.get("/items/{item_id}")
    async def get_item(item_id: int) -> dict[str, int]:
        return {"id": item_id}

    @application.get("/failure")
    async def fail() -> None:
        raise RuntimeError("database password must not be exposed")

    validation = asyncio.run(get(application, "/items/not-an-integer"))
    failure = asyncio.run(
        get(application, "/failure", raise_app_exceptions=False)
    )

    assert validation.status_code == 422
    assert validation.json()["code"] == "validation_error"
    assert validation.json()["message"] == "Request validation failed"
    assert validation.json()["details"][0]["loc"] == ["path", "item_id"]
    assert failure.status_code == 500
    assert failure.json()["code"] == "internal_server_error"
    assert failure.json()["message"] == "An unexpected error occurred"
    assert "password" not in failure.text


def test_error_details_do_not_echo_sensitive_inputs() -> None:
    application = FastAPI()
    install_exception_handlers(application)

    @application.get("/secret/{value}")
    async def secret(value: int) -> dict[str, int]:
        return {"value": value}

    @application.get("/api-error")
    async def api_error() -> None:
        raise APIError(
            status_code=422,
            code="invalid_credentials",
            message="Invalid credentials",
            details={
                "password_hash": "do-not-return",
                "token_hash": "do-not-return",
                "content": "do-not-return",
                "storage_path": "do-not-return",
                "field": "password",
            },
        )

    @application.get("/custom-validation")
    async def custom_validation() -> None:
        raise RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("body", "password"),
                    "msg": "Value error, plain-secret",
                    "input": "plain-secret",
                    "ctx": {"error": ValueError("plain-secret")},
                }
            ]
        )

    @application.get("/scalar-details")
    async def scalar_details() -> None:
        raise APIError(
            status_code=422,
            code="invalid",
            message="Invalid request",
            details="plain-secret",
        )

    @application.get("/list-details")
    async def list_details() -> None:
        raise APIError(
            status_code=422,
            code="invalid",
            message="Invalid request",
            details=["plain-secret"],
        )

    validation = asyncio.run(get(application, "/secret/do-not-return"))
    expected = asyncio.run(get(application, "/api-error"))
    custom = asyncio.run(get(application, "/custom-validation"))
    scalar = asyncio.run(get(application, "/scalar-details"))
    unkeyed_list = asyncio.run(get(application, "/list-details"))

    assert "do-not-return" not in validation.text
    assert "input" not in validation.text
    assert "do-not-return" not in expected.text
    assert expected.json()["details"] == {"field": "password"}
    assert "plain-secret" not in custom.text
    assert custom.json()["details"] == [
        {"type": "value_error", "loc": ["body", "password"]}
    ]
    assert "plain-secret" not in scalar.text
    assert "details" not in scalar.json()
    assert "plain-secret" not in unkeyed_list.text
    assert "details" not in unkeyed_list.json()


def test_portal_and_legacy_adapters_share_error_contract(
    tmp_path: Path,
) -> None:
    application = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
    )

    portal_error = asyncio.run(get(application, "/missing"))
    notes_error = asyncio.run(
        get(application, "/apps/notes/api/notes/not-an-integer")
    )
    calendar_error = asyncio.run(
        get(application, "/apps/calendar/api/tasks/not-an-integer")
    )

    assert portal_error.status_code == 404
    assert portal_error.json()["code"] == "not_found"
    assert notes_error.status_code == 404
    assert notes_error.json()["code"] == "not_found"
    assert calendar_error.status_code == 405
    assert calendar_error.json()["code"] == "method_not_allowed"
