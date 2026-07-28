from __future__ import annotations

import os
import re
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

VIEWPORTS = [
    (320, 800),
    (375, 667),
    (768, 1024),
    (1024, 768),
    (1440, 900),
]


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
        tasks = httpx.get(
            f"{base_url}/apps/calendar/api/tasks",
            timeout=1,
        ).json()
        if tasks != []:
            raise RuntimeError("Playwright test server did not use its empty temporary DB")
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
    expect(page.locator("body")).to_have_class(re.compile(r"\bsidebar-open\b"))
    expect(page.locator("#sidebar-close")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("body")).not_to_have_class(re.compile(r"\bsidebar-open\b"))
    expect(open_button).to_be_focused()
    assert external_requests == []


def test_calendar_nested_route_uses_only_local_assets(page: Page, live_server: str) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    failed_requests: list[str] = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))

    page.goto(f"{live_server}/apps/calendar/")
    expect(page.locator("#sidebar")).to_be_attached()
    expect(page.locator(".workspace-header")).to_be_visible()
    expect(page.locator('[data-app-id="calendar"][aria-current="page"]')).to_be_attached()
    expect(page.locator("#calendar")).to_be_visible()
    expect(page.locator("#create-menu-button")).to_be_visible()
    assert failed_requests == []
    assert external_requests == []


def test_calendar_resizes_after_desktop_sidebar_collapse(
    page: Page, live_server: str
) -> None:
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server}/apps/calendar/")
    calendar = page.locator(".calendar-shell")
    before = calendar.bounding_box()
    assert before is not None

    collapse = page.locator("#sidebar-collapse")
    collapse.click()
    expect(page.locator("body")).to_have_class(re.compile(r"\bsidebar-collapsed\b"))
    expect(collapse).to_have_attribute("aria-expanded", "false")
    expect(page.get_by_role("button", name="사이드바 펼치기")).to_be_visible()
    page.wait_for_timeout(300)

    after = calendar.bounding_box()
    assert after is not None
    assert after["width"] > before["width"] + 100


def test_calendar_mobile_drawer_focus_backdrop_and_escape(
    page: Page, live_server: str
) -> None:
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(f"{live_server}/apps/calendar/")
    opener = page.get_by_role("button", name="사이드바 열기")

    opener.click()
    expect(opener).to_have_attribute("aria-expanded", "true")
    expect(page.locator("#sidebar")).to_have_attribute("aria-hidden", "false")
    expect(page.locator("#sidebar-close")).to_be_focused()
    assert page.evaluate("getComputedStyle(document.body).overflow") == "hidden"

    page.locator("#sidebar-scrim").click(position={"x": 315, "y": 400})
    expect(opener).to_have_attribute("aria-expanded", "false")
    expect(opener).to_be_focused()

    opener.click()
    page.keyboard.press("Escape")
    expect(opener).to_have_attribute("aria-expanded", "false")
    expect(opener).to_be_focused()


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_calendar_viewport_layout_and_request_health(
    page: Page,
    live_server: str,
    width: int,
    height: int,
) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{live_server}/apps/calendar/")
    page.wait_for_timeout(250)

    metrics = page.evaluate(
        """() => ({
          innerWidth: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          bodyHeight: document.body.scrollHeight,
          viewportHeight: document.body.clientHeight,
          calendarWidth: document.querySelector('.calendar-shell').getBoundingClientRect().width,
          mainWidth: document.querySelector('#workspace-main').getBoundingClientRect().width,
          datePanelWidth: document.querySelector('#date-panel').getBoundingClientRect().width,
        })"""
    )
    assert metrics["documentWidth"] == metrics["innerWidth"]
    assert metrics["bodyHeight"] == metrics["viewportHeight"]
    assert 0 < metrics["calendarWidth"] <= metrics["mainWidth"]
    assert 0 < metrics["datePanelWidth"] <= metrics["mainWidth"]
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


def test_calendar_event_opens_from_keyboard(page: Page, live_server: str) -> None:
    response = page.request.post(
        f"{live_server}/apps/calendar/api/tasks",
        data={
            "title": "키보드 일정",
            "description": "",
            "status": "todo",
            "all_day": False,
            "start_at": "2026-07-28T09:00:00+09:00",
            "due_at": "2026-07-28T10:00:00+09:00",
            "topic_ids": [],
        },
    )
    assert response.ok
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{live_server}/apps/calendar/")

    event = page.get_by_role("button", name="일정 열기: 키보드 일정")
    expect(event).to_have_attribute("tabindex", "0")
    expect(event).to_have_attribute("aria-label", "일정 열기: 키보드 일정")
    event.focus()
    page.keyboard.press("Enter")
    expect(page.locator("#task-dialog")).to_be_visible()
