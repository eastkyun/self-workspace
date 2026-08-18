from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Page, expect

from apps.notes.app.database import initialize_database as initialize_notes_database
from job_platform.database import initialize_database as initialize_jobs_database
from web_collector.database import initialize_database as initialize_web_database


VIEWPORTS = (
    (320, 800),
    (375, 667),
    (768, 1024),
    (1024, 768),
    (1440, 900),
)


class JobsServer(str):
    workspace_id: str
    cookies: dict[str, str]

    def __new__(
        cls, base_url: str, *, workspace_id: str, cookies: dict[str, str]
    ):
        value = super().__new__(cls, base_url)
        value.workspace_id = workspace_id
        value.cookies = cookies
        return value


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture(scope="module")
def jobs_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[JobsServer]:
    root = tmp_path_factory.mktemp("jobs-e2e")
    jobs_database = initialize_jobs_database(root / "jobs.db")
    initialize_web_database(jobs_database)
    initialize_notes_database(root / "notes.db")
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "CALENDAR_DB_PATH": str(root / "calendar.db"),
        "NOTES_DB_PATH": str(root / "notes.db"),
        "PUBLISHING_DB_PATH": str(root / "publishing.db"),
        "ASSET_DB_PATH": str(root / "assets.db"),
        "IDENTITY_DB_PATH": str(root / "identity.db"),
        "PUBLICATION_TYPES_PATH": str(root / "publication-types.json"),
        "JOBS_DB_PATH": str(jobs_database),
        "JOBS_ENABLED": "true",
        "JOBS_WEB_COLLECTION_ENABLED": "false",
        "PUBLIC_ORIGIN": base_url,
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "portal.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(200):
            if process.poll() is not None:
                raise RuntimeError("Jobs E2E server stopped during startup")
            try:
                if httpx.get(f"{base_url}/login", timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        else:
            raise RuntimeError("Jobs E2E server did not become ready")

        session = httpx.Client(base_url=base_url, timeout=3)
        signup = session.post(
            "/api/v1/auth/signup",
            headers={"Origin": base_url},
            json={
                "email": "jobs-e2e@example.com",
                "password": "jobs-e2e-password",
                "display_name": "Jobs E2E",
            },
        )
        signup.raise_for_status()
        credentials = signup.json()
        workspace_id = credentials["workspace_id"]
        enabled = session.patch(
            f"/api/v1/workspaces/{workspace_id}/apps/jobs",
            headers={
                "Origin": base_url,
                "X-CSRF-Token": credentials["csrf_token"],
            },
            json={"enabled": True},
        )
        enabled.raise_for_status()
        yield JobsServer(
            base_url,
            workspace_id=workspace_id,
            cookies=dict(session.cookies),
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(autouse=True)
def authenticated_page(page: Page, jobs_server: JobsServer) -> None:
    page.context.add_cookies(
        [
            {"name": name, "value": value, "url": str(jobs_server)}
            for name, value in jobs_server.cookies.items()
        ]
    )


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_jobs_dialogs_fit_supported_viewports(
    page: Page, jobs_server: JobsServer, width: int, height: int
) -> None:
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text)
            if message.type == "error"
            else None
        ),
    )
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{jobs_server}/w/{jobs_server.workspace_id}/jobs")

    expect(
        page.locator("#jobs-app").get_by_role("heading", name="Jobs", exact=True)
    ).to_be_visible()
    expect(page.get_by_role("button", name="새 Job")).to_be_visible()
    expect(page.get_by_role("button", name="정리 방식 관리")).to_be_visible()
    metrics = page.evaluate(
        """() => ({
          viewport: innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          appWidth: document.querySelector('#jobs-app').scrollWidth,
        })"""
    )
    assert metrics["documentWidth"] <= metrics["viewport"] + 1
    assert metrics["appWidth"] <= metrics["viewport"] + 1

    page.get_by_role("button", name="새 Job").click()
    create_dialog = page.locator("#jobs-create-dialog")
    expect(create_dialog).to_be_visible()
    create_box = create_dialog.bounding_box()
    assert create_box is not None
    assert create_box["x"] >= 0 and create_box["y"] >= 0
    assert create_box["x"] + create_box["width"] <= width + 1
    assert create_box["y"] + create_box["height"] <= height + 1
    expect(page.locator("#jobs-form .jobs-form-scroll")).to_be_visible()
    page.locator("#job-cancel").click()

    page.get_by_role("button", name="정리 방식 관리").click()
    formatter_dialog = page.locator("#jobs-formatter-dialog")
    expect(formatter_dialog).to_be_visible()
    expect(
        formatter_dialog.get_by_role("button", name="AI 금융시장 브리핑")
    ).to_be_visible()
    formatter_box = formatter_dialog.bounding_box()
    assert formatter_box is not None
    assert formatter_box["x"] >= 0 and formatter_box["y"] >= 0
    assert formatter_box["x"] + formatter_box["width"] <= width + 1
    assert formatter_box["y"] + formatter_box["height"] <= height + 1
    page.locator("#jobs-formatter-close").click()

    assert failed_requests == []
    assert console_errors == []


def test_workspace_apps_render_the_same_jobs_menu(
    page: Page, jobs_server: JobsServer
) -> None:
    expected_href = f"/w/{jobs_server.workspace_id}/jobs"
    for route in ("notes", "calendar", "publications", "jobs"):
        page.goto(f"{jobs_server}/w/{jobs_server.workspace_id}/{route}")
        expect(
            page.locator(f'a[data-app-id="jobs"][href="{expected_href}"]')
        ).to_have_count(1)
        expect(
            page.locator(
                f'.workspace-launcher-item[data-app-id="jobs"] '
                f'a[href="{expected_href}"]'
            )
        ).to_have_count(1)
