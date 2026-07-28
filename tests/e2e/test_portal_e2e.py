from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from playwright.sync_api import Page, expect


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    port = available_port()
    database_path = tmp_path_factory.mktemp("playwright") / "calendar.db"
    environment = {
        **os.environ,
        "CALENDAR_DB_PATH": str(database_path),
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
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("Playwright test server stopped during startup")
            try:
                if httpx.get(base_url, timeout=0.2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Playwright test server did not become ready")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def assert_same_origin_requests(page: Page, base_url: str) -> list[str]:
    external_requests: list[str] = []
    expected_origin = urlparse(base_url).netloc

    def inspect_request(request) -> None:
        parsed = urlparse(request.url)
        if parsed.scheme in {"http", "https"} and parsed.netloc != expected_origin:
            external_requests.append(request.url)

    page.on("request", inspect_request)
    return external_requests


def test_desktop_launcher_and_pin_persistence(page: Page, live_server: str) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(live_server)

    expect(page.get_by_role("heading", name="고정한 앱")).to_be_visible()
    expect(page.locator("#pinned-apps").get_by_text("Calendar")).to_be_visible()
    page.get_by_role("button", name="앱 런처 열기").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.get_by_role("button", name="앱 런처 닫기").click()

    page.locator("#pinned-apps").get_by_role(
        "button", name="Calendar 고정 해제"
    ).click()
    expect(page.locator("#pinned-empty")).to_be_visible()
    page.reload()
    expect(page.locator("#pinned-empty")).to_be_visible()
    assert external_requests == []


def test_mobile_sidebar_collapses_and_opens(page: Page, live_server: str) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(live_server)

    open_button = page.get_by_role("button", name="사이드바 열기")
    expect(open_button).to_be_visible()
    open_button.click()
    expect(page.locator("body")).to_have_class("sidebar-open")
    expect(page.locator("#sidebar-close")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("body")).not_to_have_class("sidebar-open")
    expect(open_button).to_be_focused()
    assert external_requests == []


def test_calendar_nested_route_uses_only_local_assets(page: Page, live_server: str) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    failed_requests: list[str] = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))

    page.goto(f"{live_server}/apps/calendar/")
    expect(page.locator("#calendar")).to_be_visible()
    expect(page.locator("#create-menu-button")).to_be_visible()
    assert failed_requests == []
    assert external_requests == []
