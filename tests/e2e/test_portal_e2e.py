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
    notes_database_path = database_path.parent / "notes.db"
    environment = {
        **os.environ,
        "CALENDAR_DB_PATH": str(database_path),
        "NOTES_DB_PATH": str(notes_database_path),
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
        notes = httpx.get(
            f"{base_url}/apps/notes/api/notes",
            timeout=1,
        ).json()
        if notes != []:
            raise RuntimeError("Playwright test server did not use its empty temporary Notes DB")
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
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on(
        "requestfailed",
        lambda request: failed_requests.append(
            f"{request.method} {request.failure} {request.url}"
        ),
    )
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(live_server)

    expect(page.get_by_role("heading", name="고정한 앱")).to_be_visible()
    expect(page.locator("#pinned-apps").get_by_text("Calendar")).to_be_visible()
    expect(
        page.locator("#pinned-apps .hub-app-name").get_by_text("Notes", exact=True)
    ).to_be_visible()
    page.get_by_role("button", name="앱 런처 열기").click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.get_by_role("button", name="앱 런처 닫기").click()

    page.locator("#pinned-apps").get_by_role(
        "button", name="Calendar 고정 해제"
    ).click()
    expect(
        page.locator("#pinned-apps .hub-app-name").get_by_text("Notes", exact=True)
    ).to_be_visible()
    page.reload()
    expect(
        page.locator("#pinned-apps .hub-app-name").get_by_text("Notes", exact=True)
    ).to_be_visible()
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


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


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_hub_quote_viewport_layout_and_request_health(
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
    page.goto(live_server)

    quote = page.locator(".hub-quote")
    expect(quote).to_be_visible()
    expect(quote.locator("cite")).not_to_be_empty()
    metrics = page.evaluate(
        """() => ({
          innerWidth: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          quoteWidth: document.querySelector('.hub-quote').getBoundingClientRect().width,
          mainWidth: document.querySelector('#workspace-main').getBoundingClientRect().width,
        })"""
    )
    assert metrics["documentWidth"] == metrics["innerWidth"]
    assert 0 < metrics["quoteWidth"] <= metrics["mainWidth"]
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


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


def test_calendar_shared_markdown_compact_editor_and_viewer(
    page: Page,
    live_server: str,
) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on(
        "requestfailed",
        lambda request: failed_requests.append(
            f"{request.method} {request.failure} {request.url}"
        ),
    )
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )
    page.goto(f"{live_server}/apps/calendar/")
    page.locator("#create-menu-button").click()
    page.locator("#new-task-button").click()
    dialog = page.locator("#task-dialog")
    dialog.locator("#task-title").fill("Markdown 일정")
    description = dialog.locator("#task-description")
    description.fill("중요 일정")
    description.evaluate(
        "(element) => element.setSelectionRange(0, element.value.length)"
    )
    dialog.get_by_role("button", name="굵게").click()
    expect(description).to_have_value("**중요 일정**")
    description.focus()
    description.press("End")
    description.press("Enter")
    description.type("/h2")
    expect(page.locator(".markdown-slash-palette")).to_be_visible()
    description.press("Enter")
    description.type("준비 사항")
    description.press("Enter")
    description.type("<script>window.__calendarMarkdownXss = true</script>")
    dialog.get_by_role("button", name="미리보기").click()
    preview = dialog.locator("#task-markdown-preview")
    expect(preview.locator("strong")).to_have_text("중요 일정")
    expect(preview.locator("h2")).to_have_text("준비 사항")
    expect(preview.locator("script")).to_have_count(0)
    expect(preview).to_contain_text(
        "<script>window.__calendarMarkdownXss = true</script>"
    )
    assert page.evaluate("window.__calendarMarkdownXss") is None
    dialog.get_by_role("button", name="저장").click()
    expect(dialog).to_be_hidden()

    page.get_by_role("button", name="일정 열기: Markdown 일정").click()
    expect(dialog.locator("#task-description")).to_be_hidden()
    expect(preview.locator("strong")).to_have_text("중요 일정")
    expect(preview.locator("h2")).to_have_text("준비 사항")
    expect(preview.locator("script")).to_have_count(0)
    assert page.evaluate("window.__calendarMarkdownXss") is None

    dialog.get_by_role("button", name="미리보기").click()
    expect(dialog.locator("#task-description")).to_be_visible()
    dialog.locator("#task-description").press("End")
    dialog.locator("#task-description").type("\n\n수정됨")
    dialog.get_by_role("button", name="저장").click()
    page.get_by_role("button", name="일정 열기: Markdown 일정").click()
    expect(preview).to_contain_text("수정됨")
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


def test_notes_registry_surfaces_and_crud(page: Page, live_server: str) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on(
        "requestfailed",
        lambda request: failed_requests.append(
            f"{request.method} {request.failure} {request.url}"
        ),
    )
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )
    page.goto(live_server)
    expect(
        page.locator("#all-apps .hub-app-name").get_by_text("Notes", exact=True)
    ).to_be_visible()
    page.get_by_role("link", name="Notes").first.click()
    expect(page.locator('[data-app-id="notes"][aria-current="page"]')).to_be_attached()

    page.get_by_role("button", name="새 메모", exact=True).click()
    page.locator("#note-title").fill("E2E 일반 메모")
    page.locator("#note-content").fill("검색 가능한 본문")
    page.get_by_role("button", name="고정").click()
    page.get_by_role("button", name="저장").click()
    expect(page.locator("#notes-announcer")).to_contain_text("저장")
    expect(page).to_have_url(re.compile(r"note_id=\d+"))
    expect(page.locator("#note-view-title")).to_have_text("E2E 일반 메모")
    expect(page.locator("#note-form")).to_be_hidden()
    page.wait_for_timeout(250)
    page.reload()
    expect(page.locator("#note-view-title")).to_have_text("E2E 일반 메모")
    page.get_by_role("button", name="편집", exact=True).click()
    expect(page.locator("#note-title")).to_have_value("E2E 일반 메모")
    expect(page.get_by_role("button", name="고정 해제")).to_be_visible()

    page.locator("#notes-search").fill("검색 가능한")
    page.wait_for_timeout(250)
    expect(page.get_by_role("option", name=re.compile("E2E 일반 메모"))).to_be_visible()
    page.once("dialog", lambda dialog: dialog.accept())
    page.get_by_role("button", name="삭제").click()
    expect(page.locator("#notes-announcer")).to_contain_text("삭제")
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


def test_notes_shared_markdown_editor_preview_and_viewer(
    page: Page,
    live_server: str,
) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on(
        "requestfailed",
        lambda request: failed_requests.append(
            f"{request.method} {request.failure} {request.url}"
        ),
    )
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )

    page.goto(f"{live_server}/apps/notes/")
    page.get_by_role("button", name="새 메모", exact=True).click()
    page.locator("#note-title").fill("Markdown E2E")
    content = page.locator("#note-content")
    content.fill("굵게")
    content.evaluate(
        "(element) => element.setSelectionRange(0, element.value.length)"
    )
    page.get_by_role("button", name="굵게").click()
    expect(content).to_have_value("**굵게**")
    content.focus()
    content.press("End")
    content.press("Enter")
    content.type("/h2")
    expect(page.locator(".markdown-slash-palette")).to_be_visible()
    content.press("Enter")
    content.type("섹션")
    content.press("Enter")
    content.type("<script>window.__markdownXss = true</script>")
    page.get_by_role("button", name=re.compile(r"^미리보기")).click()
    preview = page.locator("#note-markdown-preview")
    expect(preview.locator("strong")).to_have_text("굵게")
    expect(preview.locator("h2")).to_have_text("섹션")
    expect(preview.locator("script")).to_have_count(0)
    expect(preview).to_contain_text(
        "<script>window.__markdownXss = true</script>"
    )
    assert page.evaluate("window.__markdownXss") is None

    page.get_by_role("button", name="저장").click()
    expect(page.locator("#notes-announcer")).to_contain_text("저장")
    viewer = page.locator("#note-view-content")
    expect(viewer.locator("strong")).to_have_text("굵게")
    expect(viewer.locator("h2")).to_have_text("섹션")
    expect(viewer.locator("script")).to_have_count(0)
    expect(viewer).to_contain_text(
        "<script>window.__markdownXss = true</script>"
    )
    assert page.evaluate("window.__markdownXss") is None
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


def test_calendar_creates_linked_meeting_note(page: Page, live_server: str) -> None:
    task = page.request.post(
        f"{live_server}/apps/calendar/api/tasks",
        data={
            "title": "Notes 연계 회의",
            "description": "",
            "status": "todo",
            "all_day": False,
            "start_at": "2026-07-29T09:00:00+09:00",
            "due_at": "2026-07-29T10:00:00+09:00",
            "topic_ids": [],
        },
    ).json()
    page.goto(f"{live_server}/apps/calendar/")
    page.get_by_role("button", name="일정 열기: Notes 연계 회의").click()
    page.get_by_role("link", name="회의록 작성").click()
    expect(page).to_have_url(re.compile(r"/apps/notes/new\?.*type=meeting_minutes"))
    expect(page.locator("#note-context")).to_contain_text(f"일정 #{task['id']}")
    page.locator("#note-title").fill("Notes 연계 회의록")
    page.get_by_role("button", name="저장").click()
    expect(page.locator("#view-note-context")).to_contain_text("Notes 연계 회의")

    linked = page.request.get(
        f"{live_server}/apps/notes/api/notes",
        params={
            "source_app": "calendar",
            "source_type": "event",
            "source_id": str(task["id"]),
        },
    ).json()
    assert [note["title"] for note in linked] == ["Notes 연계 회의록"]


def test_calendar_attaches_and_unlinks_existing_note(
    page: Page, live_server: str
) -> None:
    note = page.request.post(
        f"{live_server}/apps/notes/api/notes",
        data={"title": "기존 연결 노트", "content": "", "note_type": "general"},
    ).json()
    task = page.request.post(
        f"{live_server}/apps/calendar/api/tasks",
        data={
            "title": "기존 노트 연결 일정",
            "description": "",
            "status": "todo",
            "all_day": False,
            "start_at": "2026-07-29T11:00:00+09:00",
            "due_at": "2026-07-29T12:00:00+09:00",
            "topic_ids": [],
        },
    ).json()
    page.goto(f"{live_server}/apps/calendar/")
    page.get_by_role("button", name="일정 열기: 기존 노트 연결 일정").click()
    page.get_by_role("button", name="기존 노트 연결", exact=True).click()
    expect(page.locator("#attach-note-dialog")).to_be_visible()
    page.locator("#attach-note-search").fill("기존 연결 노트")
    page.locator("#attach-note-results").get_by_role(
        "button", name=re.compile("기존 연결 노트")
    ).click()
    expect(page.locator("#linked-notes-list")).to_contain_text("기존 연결 노트")
    page.get_by_role("button", name="기존 연결 노트 연결 해제").click()
    expect(page.locator("#linked-notes-status")).to_contain_text("없습니다")
    assert page.request.get(
        f"{live_server}/apps/notes/api/notes/{note['id']}/links"
    ).json() == []
    assert page.request.get(
        f"{live_server}/apps/calendar/api/tasks"
    ).json()
    assert task["id"] > 0


def test_deleted_calendar_event_leaves_orphaned_note(
    page: Page, live_server: str
) -> None:
    task = page.request.post(
        f"{live_server}/apps/calendar/api/tasks",
        data={
            "title": "삭제 후 snapshot 일정",
            "description": "",
            "status": "todo",
            "all_day": False,
            "start_at": "2026-07-29T13:00:00+09:00",
            "due_at": "2026-07-29T14:00:00+09:00",
            "topic_ids": [],
        },
    ).json()
    note = page.request.post(
        f"{live_server}/apps/notes/api/notes",
        data={
            "title": "보존되는 노트",
            "content": "",
            "note_type": "research_note",
            "links": [{
                "source_app": "calendar",
                "source_type": "event",
                "source_id": str(task["id"]),
            }],
        },
    ).json()
    assert page.request.delete(
        f"{live_server}/apps/calendar/api/tasks/{task['id']}"
    ).status == 204

    page.goto(f"{live_server}/apps/notes/?note_id={note['id']}")
    expect(page.locator("#note-view-title")).to_have_text("보존되는 노트")
    expect(page.locator("#view-note-context")).to_contain_text("삭제 후 snapshot 일정")
    expect(page.locator("#view-note-context")).to_contain_text("삭제된 일정")


def test_notes_browser_back_restores_list_and_focus(
    page: Page, live_server: str
) -> None:
    note = page.request.post(
        f"{live_server}/apps/notes/api/notes",
        data={"title": "Back 복원 노트", "content": "", "note_type": "general"},
    ).json()
    page.goto(f"{live_server}/apps/notes/")
    expect(page.locator("#notes-empty")).to_be_visible()
    expect(page.locator("#note-form")).to_be_hidden()
    page.get_by_role("option", name=re.compile("Back 복원 노트")).click()
    expect(page).to_have_url(re.compile(rf"note_id={note['id']}"))
    expect(page.locator("#note-view-title")).to_have_text("Back 복원 노트")
    expect(page.locator("#notes-empty")).to_be_hidden()
    expect(page.locator("#note-view")).to_be_visible()
    expect(page.locator("#note-form")).to_be_hidden()

    page.go_back()
    expect(page).to_have_url(re.compile(r"/apps/notes/$"))
    expect(page.locator("#notes-empty")).to_be_visible()
    expect(page.locator("#note-form")).to_be_hidden()
    expect(page.locator("#notes-search")).to_be_focused()

    page.locator("#new-note").click()
    expect(page).to_have_url(re.compile(r"/apps/notes/new$"))
    expect(page.locator("#note-form")).to_be_visible()
    expect(page.locator("#note-view")).to_be_hidden()

    page.go_back()
    expect(page).to_have_url(re.compile(r"/apps/notes/$"))
    expect(page.locator("#notes-empty")).to_be_visible()

    page.go_forward()
    expect(page).to_have_url(re.compile(r"/apps/notes/new$"))
    expect(page.locator("#note-form")).to_be_visible()


@pytest.mark.parametrize(
    ("width", "height", "two_pages"),
    [(1440, 900, True), (1024, 768, True), (768, 1024, False), (375, 667, False)],
)
def test_calendar_task_book_layout_and_link_action_alignment(
    page: Page,
    live_server: str,
    width: int,
    height: int,
    two_pages: bool,
) -> None:
    title = f"Book layout {width}"
    day = {1440: 20, 1024: 21, 768: 22, 375: 23}[width]
    page.request.post(
        f"{live_server}/apps/calendar/api/tasks",
        data={
            "title": title,
            "description": "",
            "status": "todo",
            "all_day": False,
            "start_at": f"2026-07-{day:02d}T15:00:00+09:00",
            "due_at": f"2026-07-{day:02d}T16:00:00+09:00",
            "topic_ids": [],
        },
    )
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{live_server}/apps/calendar/")
    page.get_by_role("button", name=f"일정 열기: {title}").click()
    expect(page.locator("#task-dialog")).to_be_visible()
    metrics = page.evaluate(
        """() => {
          const left = document.querySelector('.task-page-primary').getBoundingClientRect();
          const right = document.querySelector('.task-page-schedule').getBoundingClientRect();
          const action = getComputedStyle(document.querySelector('#create-meeting-note'));
          return {
            sameRow: Math.abs(left.top - right.top) < 2,
            actionAlign: action.alignItems,
            actionJustify: action.justifyContent,
            documentWidth: document.documentElement.scrollWidth,
            innerWidth: window.innerWidth,
          };
        }"""
    )
    assert metrics["sameRow"] is two_pages
    assert metrics["actionAlign"] == "center"
    assert metrics["actionJustify"] == "center"
    assert metrics["documentWidth"] == metrics["innerWidth"]


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_notes_viewport_layout_and_request_health(
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
    page.goto(f"{live_server}/apps/notes/")
    page.wait_for_timeout(250)
    metrics = page.evaluate(
        """() => ({
          innerWidth: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          bodyHeight: document.body.scrollHeight,
          viewportHeight: document.body.clientHeight,
          workspaceWidth: document.querySelector('.notes-workspace').getBoundingClientRect().width,
          mainWidth: document.querySelector('#workspace-main').getBoundingClientRect().width,
        })"""
    )
    assert metrics["documentWidth"] == metrics["innerWidth"]
    assert metrics["bodyHeight"] == metrics["viewportHeight"]
    assert 0 < metrics["workspaceWidth"] <= metrics["mainWidth"]
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []
