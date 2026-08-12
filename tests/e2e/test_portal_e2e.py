from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

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


class LiveServer(str):
    workspace_id: str
    csrf_token: str
    cookies: dict[str, str]

    def __new__(
        cls,
        base_url: str,
        *,
        workspace_id: str,
        csrf_token: str,
        cookies: dict[str, str],
    ):
        value = super().__new__(cls, base_url)
        value.workspace_id = workspace_id
        value.csrf_token = csrf_token
        value.cookies = cookies
        return value


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LiveServer]:
    port = available_port()
    database_path = tmp_path_factory.mktemp("playwright") / "calendar.db"
    notes_database_path = database_path.parent / "notes.db"
    publishing_database_path = database_path.parent / "publishing.db"
    asset_database_path = database_path.parent / "assets.db"
    identity_database_path = database_path.parent / "identity.db"
    publication_types_path = database_path.parent / "publication-types.json"
    environment = {
        **os.environ,
        "CALENDAR_DB_PATH": str(database_path),
        "NOTES_DB_PATH": str(notes_database_path),
        "PUBLISHING_DB_PATH": str(publishing_database_path),
        "ASSET_DB_PATH": str(asset_database_path),
        "IDENTITY_DB_PATH": str(identity_database_path),
        "PUBLICATION_TYPES_PATH": str(publication_types_path),
        "PUBLIC_ORIGIN": f"http://127.0.0.1:{port}",
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
        session = httpx.Client(base_url=base_url, timeout=2)
        signup = session.post(
            "/api/v1/auth/signup",
            headers={"Origin": base_url},
            json={
                "email": "playwright@example.com",
                "password": "playwright-password",
                "display_name": "Playwright User",
            },
        )
        signup.raise_for_status()
        credentials = signup.json()
        workspace_id = credentials["workspace_id"]
        tasks = session.get(
            f"/api/v1/workspaces/{workspace_id}/calendar/tasks",
        ).json()
        if tasks != []:
            raise RuntimeError("Playwright test server did not use its empty temporary DB")
        notes = session.get(
            f"/api/v1/workspaces/{workspace_id}/notes",
        ).json()
        if notes != []:
            raise RuntimeError("Playwright test server did not use its empty temporary Notes DB")
        publications = session.get(
            f"/api/v1/workspaces/{workspace_id}/publications"
        ).json()
        if publications != []:
            raise RuntimeError(
                "Playwright test server did not use its empty temporary Publishing DB"
            )
        yield LiveServer(
            base_url,
            workspace_id=workspace_id,
            csrf_token=credentials["csrf_token"],
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
def authenticated_page(page: Page, live_server: LiveServer) -> None:
    page.context.add_cookies(
        [
            {"name": name, "value": value, "url": str(live_server)}
            for name, value in live_server.cookies.items()
        ]
    )
    page.context.set_extra_http_headers(
        {"X-CSRF-Token": live_server.csrf_token}
    )


def csrf_headers(live_server: LiveServer) -> dict[str, str]:
    return {
        "Origin": str(live_server),
        "X-CSRF-Token": live_server.csrf_token,
    }


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
    page.goto(f"{live_server}/hub")

    expect(page.get_by_role("heading", name="고정한 앱")).to_be_visible()
    expect(page.locator("#workspace-guest-actions")).to_be_hidden()
    expect(page.locator("#workspace-account")).to_be_visible()
    page.locator("#workspace-account-toggle").click()
    expect(page.locator("#workspace-account-email")).to_have_text(
        "playwright@example.com"
    )
    page.locator("#workspace-profile-open").click()
    expect(page.locator("#workspace-profile-dialog")).to_be_visible()
    expect(page.locator("#workspace-profile-name")).to_have_text("Playwright User")
    page.locator("#workspace-profile-close").click()
    expect(page.locator('#sidebar [data-app-id="calendar"]')).to_have_attribute(
        "href", f"/w/{live_server.workspace_id}/calendar"
    )
    expect(page.locator('#all-apps [data-app-id="notes"] a')).to_have_attribute(
        "href", f"/w/{live_server.workspace_id}/notes"
    )
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


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_workspace_home_viewport_layout_and_request_health(
    page: Page, live_server: LiveServer, width: int, height: int
) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.set_viewport_size({"width": width, "height": height})

    page.goto(f"{live_server}/w/{live_server.workspace_id}/home")
    expect(page.locator("[data-workspace-home]")).to_be_visible()
    expect(page.get_by_role("heading", name="바로 시작하기")).to_be_visible()
    expect(page.get_by_role("heading", name="나의 Dashboard")).to_be_visible()
    expect(page.get_by_role("heading", name="즐겨찾기")).to_be_visible()
    expect(page.get_by_role("heading", name="최근 본 항목")).to_be_visible()
    expect(page.get_by_role("heading", name="최근 Workspace 활동")).to_be_visible()
    expect(page.get_by_role("heading", name="Workspace 검색")).to_be_visible()
    metrics = page.evaluate(
        """() => ({
          bodyWidth: document.body.scrollWidth,
          viewportWidth: document.documentElement.clientWidth,
          homeWidth: document.querySelector('[data-workspace-home]').scrollWidth,
          homeClientWidth: document.querySelector('[data-workspace-home]').clientWidth,
        })"""
    )
    assert metrics["bodyWidth"] == metrics["viewportWidth"]
    assert metrics["homeWidth"] == metrics["homeClientWidth"]
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


def test_authenticated_root_logo_and_home_share_canonical_workspace_url(
    page: Page, live_server: LiveServer
) -> None:
    canonical = f"{live_server}/w/{live_server.workspace_id}/home"
    page.goto(str(live_server))
    expect(page).to_have_url(canonical)
    brand = page.get_by_role("link", name="My Workspace 홈")
    home = page.locator(".workspace-navigation").get_by_role("link", name="Home")
    expect(brand).to_have_attribute("href", f"/w/{live_server.workspace_id}/home")
    expect(home).to_have_attribute("href", f"/w/{live_server.workspace_id}/home")
    brand.click()
    expect(page).to_have_url(canonical)
    home.click()
    expect(page).to_have_url(canonical)


def test_workspace_search_and_dashboard_widget_configuration(
    page: Page, live_server: LiveServer
) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    note = page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/notes",
        headers=csrf_headers(live_server),
        data={
            "title": "Phase 9 Search E2E",
            "content": "Permission-aware workspace result",
            "note_type": "general",
        },
    ).json()

    page.goto(f"{live_server}/w/{live_server.workspace_id}/search")
    page.get_by_role("searchbox", name="Workspace 검색").fill("Phase 9 Search")
    page.get_by_role("button", name="검색", exact=True).click()
    expect(page.get_by_role("link", name="Phase 9 Search E2E")).to_have_attribute(
        "href", f"/w/{live_server.workspace_id}/notes?note_id={note['id']}"
    )

    page.set_viewport_size({"width": 1440, "height": 1600})
    page.goto(f"{live_server}/w/{live_server.workspace_id}/home")
    page.get_by_role("button", name="위젯 관리").click()
    search_row = page.locator("[data-layout-key='workspace.search']")
    search_row.get_by_role("button", name="숨기기").click()
    expect(page.locator("[data-widget-key='workspace.search']")).to_have_count(0)
    page.get_by_role("button", name="위젯 관리").click()
    page.locator("[data-layout-key='workspace.search']").get_by_role(
        "button", name="표시하기"
    ).click()
    search_widget = page.locator("[data-widget-key='workspace.search']")
    expect(search_widget).to_be_visible()
    drag_handle = search_widget.locator("[data-widget-drag-handle]")
    drag_handle.drag_to(
        page.locator("[data-widget-key='calendar.today']"),
        target_position={"x": 4, "y": 4},
    )
    page.wait_for_timeout(500)
    page.wait_for_load_state("networkidle")
    layout = page.request.get(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/dashboard"
    ).json()["layout"]
    keys = [item["widget_key"] for item in layout]
    assert keys.index("workspace.search") < keys.index("calendar.today")

    resize = page.locator("[data-widget-key='workspace.search'] [data-widget-resize]")
    box = resize.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2 + 110, box["y"] + box["height"] / 2)
    page.mouse.up()
    page.wait_for_timeout(500)
    page.wait_for_load_state("networkidle")
    resized = page.request.get(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/dashboard"
    ).json()["layout"]
    assert next(item for item in resized if item["widget_key"] == "workspace.search")["size"] == "MEDIUM"
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


@pytest.mark.parametrize("width", [320, 375])
def test_workspace_dashboard_and_search_contain_long_titles(
    page: Page, live_server: LiveServer, width: int
) -> None:
    """Unbroken domain titles must not create a nested horizontal scroller."""
    title = "W" * 116 + str(width)
    response = page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/notes",
        headers=csrf_headers(live_server),
        data={"title": title, "content": "long-title-overflow", "note_type": "general"},
    )
    assert response.ok
    page.set_viewport_size({"width": width, "height": 800})

    page.goto(f"{live_server}/w/{live_server.workspace_id}/home")
    expect(page.locator(".workspace-resource-list strong").filter(has_text=title).first).to_be_visible()
    home_metrics = page.evaluate(
        """() => {
          const root = document.querySelector('[data-workspace-home]');
          const panels = [...document.querySelectorAll('.workspace-dashboard-widget')];
          return {
            bodyWidth: document.body.scrollWidth,
            viewportWidth: document.documentElement.clientWidth,
            rootWidth: root.scrollWidth,
            rootClientWidth: root.clientWidth,
            overflowingPanels: panels.filter((panel) => panel.scrollWidth > panel.clientWidth).length,
          };
        }"""
    )
    assert home_metrics["bodyWidth"] == home_metrics["viewportWidth"]
    assert home_metrics["rootWidth"] == home_metrics["rootClientWidth"]
    assert home_metrics["overflowingPanels"] == 0

    page.goto(f"{live_server}/w/{live_server.workspace_id}/search")
    page.get_by_role("searchbox", name="Workspace 검색").fill(title)
    page.get_by_role("button", name="검색", exact=True).click()
    expect(page.get_by_role("link", name=title).first).to_be_visible()
    search_metrics = page.evaluate(
        """() => {
          const root = document.querySelector('[data-workspace-search]');
          const groups = [...document.querySelectorAll('.workspace-search-group')];
          return {
            bodyWidth: document.body.scrollWidth,
            viewportWidth: document.documentElement.clientWidth,
            rootWidth: root.scrollWidth,
            rootClientWidth: root.clientWidth,
            overflowingGroups: groups.filter((group) => group.scrollWidth > group.clientWidth).length,
          };
        }"""
    )
    assert search_metrics["bodyWidth"] == search_metrics["viewportWidth"]
    assert search_metrics["rootWidth"] == search_metrics["rootClientWidth"]
    assert search_metrics["overflowingGroups"] == 0


@pytest.mark.parametrize("width", [320, 1440])
def test_workspace_general_settings_is_responsive_and_saves(
    page: Page, live_server: LiveServer, width: int
) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    failed_requests: list[str] = []
    console_errors: list[str] = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(
        f"{live_server}/w/{live_server.workspace_id}/settings/general"
    )

    expect(page.locator("[data-workspace-settings]")).to_be_visible()
    expect(page.get_by_role("heading", name="일반 설정")).to_be_visible()
    expect(page.get_by_role("navigation", name="Workspace settings")).to_be_visible()
    page.get_by_label("설명").fill(f"settings-layout-{width}")
    page.get_by_role("button", name="변경사항 저장").click()
    expect(page.get_by_role("status")).to_have_text("변경사항을 저장했습니다.")

    metrics = page.evaluate(
        """() => {
          const root = document.querySelector('[data-workspace-settings]');
          const card = document.querySelector('.workspace-settings-card');
          return {
            bodyWidth: document.body.scrollWidth,
            viewportWidth: document.documentElement.clientWidth,
            rootWidth: root.scrollWidth,
            rootClientWidth: root.clientWidth,
            cardWidth: card.scrollWidth,
            cardClientWidth: card.clientWidth,
          };
        }"""
    )
    assert metrics["bodyWidth"] == metrics["viewportWidth"]
    assert metrics["rootWidth"] == metrics["rootClientWidth"]
    assert metrics["cardWidth"] == metrics["cardClientWidth"]
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


@pytest.mark.parametrize("section", ["general", "members", "apps", "roles", "permissions"])
@pytest.mark.parametrize("width", [320, 1440])
def test_every_workspace_settings_page_uses_shared_responsive_shell(
    page: Page, live_server: LiveServer, section: str, width: int
) -> None:
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/{section}")

    expect(page.locator("[data-workspace-settings]")).to_be_visible()
    expect(page.locator("#sidebar")).to_be_attached()
    expect(page.locator(".workspace-header")).to_be_visible()
    expect(page.get_by_role("navigation", name="Workspace settings")).to_be_visible()
    expect(page.get_by_role("link", name="홈으로")).to_be_visible()
    expect(
        page.locator(
            f'a[href="/w/{live_server.workspace_id}/settings/{section}"][aria-current="page"]'
        )
    ).to_have_count(1)

    metrics = page.evaluate(
        """() => {
          const root = document.querySelector('[data-workspace-settings]');
          const main = document.querySelector('#workspace-main');
          const content = document.querySelector('.workspace-settings-content');
          const reachable = content?.querySelectorAll(
            'a, button, input, select, textarea, [tabindex], [role="status"], td'
          ) || [];
          const lastTarget = reachable[reachable.length - 1] || content?.lastElementChild;
          const hasOwnerOverflow = root.scrollHeight > root.clientHeight;
          root.scrollTop = root.scrollHeight;
          const ownerScrolled = !hasOwnerOverflow || root.scrollTop > 0;
          lastTarget?.scrollIntoView({block: 'end'});
          const rootRect = root.getBoundingClientRect();
          const lastRect = lastTarget?.getBoundingClientRect();
          return {
            bodyWidth: document.body.scrollWidth,
            viewportWidth: document.documentElement.clientWidth,
            rootWidth: root.scrollWidth,
            rootClientWidth: root.clientWidth,
            mainOverflowY: getComputedStyle(main).overflowY,
            mainScrollTop: main.scrollTop,
            hasOwnerOverflow,
            ownerScrolled,
            lastTargetVisible: !lastRect ||
              (lastRect.bottom <= rootRect.bottom + 1 &&
                lastRect.bottom >= rootRect.top - 1),
          };
        }"""
    )
    assert metrics["bodyWidth"] == metrics["viewportWidth"]
    assert metrics["rootWidth"] == metrics["rootClientWidth"]
    assert metrics["mainOverflowY"] == "hidden"
    assert metrics["mainScrollTop"] == 0
    assert metrics["ownerScrolled"] is True
    assert metrics["lastTargetVisible"] is True


def test_workspace_members_invitation_reports_busy_success_and_failure(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/members")
    form = page.locator("[data-invitation-form]")
    submit = form.get_by_role("button")
    invitation_url = (
        f"**/api/v1/workspaces/{live_server.workspace_id}/invitations"
    )
    pending_routes = []
    page.route(invitation_url, lambda route: pending_routes.append(route))

    form.get_by_label("이메일").fill("settings-success@example.com")
    submit.click()
    expect(submit).to_be_disabled()
    expect(form.get_by_role("status")).to_contain_text("초대")
    assert len(pending_routes) == 1
    pending_routes[0].fulfill(
        status=201,
        content_type="application/json",
        body='{"id":"settings-invitation","delivery_status":"SENT"}',
    )
    expect(form.get_by_role("status")).to_contain_text("초대를 전송했습니다.")
    expect(submit).to_be_enabled()
    expect(form.get_by_label("이메일")).to_have_value("")

    page.unroute(invitation_url)
    page.route(
        invitation_url,
        lambda route: route.fulfill(status=500, body="failure"),
    )
    form.get_by_label("이메일").fill("settings-failure@example.com")
    submit.click()
    expect(form.get_by_role("status")).to_contain_text("요청을 처리하지 못했습니다")
    expect(form.get_by_label("이메일")).to_have_value("settings-failure@example.com")


def test_workspace_members_invitation_reports_created_but_delivery_failed(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/members")
    form = page.locator("[data-invitation-form]")
    page.route(
        f"**/api/v1/workspaces/{live_server.workspace_id}/invitations",
        lambda route: route.fulfill(
            status=201,
            content_type="application/json",
            body='{"id":"failed-delivery","delivery_status":"FAILED"}',
        ),
    )

    form.get_by_label("이메일").fill("delivery-failed@example.com")
    form.get_by_role("button").click()

    expect(form.get_by_role("status")).to_have_text(
        "초대가 생성되었지만 전송하지 못했습니다. 워크스페이스 설정에서 다시 시도해 주세요."
    )
    expect(form.get_by_role("status")).to_have_class(re.compile(r"\bis-error\b"))


def test_workspace_apps_toggle_restores_control_after_failure(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/apps")
    toggle = page.locator("[data-app-toggle]").first
    initial_enabled = toggle.get_attribute("data-enabled")
    page.route(
        f"**/api/v1/workspaces/{live_server.workspace_id}/apps/*",
        lambda route: route.fulfill(status=500, body="failure"),
    )

    toggle.click()

    expect(toggle).to_be_enabled()
    expect(page.locator("#app-settings-status")).to_contain_text(
        "앱 설정을 변경하지 못했습니다"
    )
    expect(toggle).to_have_attribute("data-enabled", initial_enabled or "")


def test_workspace_apps_excludes_required_workspace_app_toggle(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/apps")

    expect(page.locator("[data-app-toggle='workspace']")).to_have_count(0)


def test_workspace_apps_toggle_persists_after_successful_patch_and_reload(
    page: Page, live_server: LiveServer
) -> None:
    app_key = "assets"
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/apps")
    toggle = page.locator(f"[data-app-toggle='{app_key}']")
    expect(toggle).to_have_count(1)
    initial_enabled = toggle.get_attribute("data-enabled")
    expected_enabled = "false" if initial_enabled == "true" else "true"

    try:
        toggle.click()
        expect(page.locator(f"[data-app-toggle='{app_key}']")).to_have_attribute(
            "data-enabled", expected_enabled
        )
        page.reload()
        expect(page.locator(f"[data-app-toggle='{app_key}']")).to_have_attribute(
            "data-enabled", expected_enabled
        )
    finally:
        httpx.patch(
            f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/apps/{app_key}",
            headers={
                "Origin": str(live_server),
                "X-CSRF-Token": live_server.csrf_token,
            },
            cookies=live_server.cookies,
            json={"enabled": initial_enabled == "true"},
            timeout=2,
        ).raise_for_status()


def test_workspace_roles_page_is_explicitly_read_only(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/roles")

    expect(page.get_by_text("읽기 전용", exact=True)).to_be_visible()
    expect(page.locator("[data-role-matrix] button")).to_have_count(0)
    expect(page.locator("[data-role-matrix] input")).to_have_count(0)


def test_workspace_permissions_matrix_is_keyboard_reachable_local_scroll_region(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/permissions")

    matrix = page.locator("[data-permission-matrix]")
    expect(matrix).to_have_attribute("role", "region")
    expect(matrix).to_have_attribute("tabindex", "0")
    expect(matrix).to_have_attribute("aria-labelledby", re.compile(".+"))
    matrix.focus()
    expect(matrix).to_be_focused()
    assert page.evaluate("document.body.scrollWidth") == page.evaluate(
        "document.documentElement.clientWidth"
    )


def _insert_mutable_permission_override(
    page: Page, workspace_id: str, initial: str
) -> None:
    page.evaluate(
        """({workspaceId, initial}) => {
          const matrix = document.querySelector('[data-permission-matrix]');
          const select = document.createElement('select');
          select.dataset.permissionOverride = '';
          select.dataset.workspaceId = workspaceId;
          select.dataset.memberId = 'test-member';
          select.dataset.permissionCode = 'notes.create';
          select.dataset.previous = initial;
          for (const value of ['', 'ALLOW', 'DENY']) {
            const option = document.createElement('option');
            option.value = value;
            option.textContent = value || 'DEFAULT';
            select.append(option);
          }
          select.value = initial;
          matrix.append(select);
        }""",
        {"workspaceId": workspace_id, "initial": initial},
    )


@pytest.mark.parametrize(
    ("initial", "next_value", "expected_method"),
    [("", "ALLOW", "PUT"), ("ALLOW", "", "DELETE")],
)
def test_workspace_permission_override_saves_put_and_delete_changes(
    page: Page,
    live_server: LiveServer,
    initial: str,
    next_value: str,
    expected_method: str,
) -> None:
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/permissions")
    _insert_mutable_permission_override(page, live_server.workspace_id, initial)
    requests: list[str] = []
    page.route(
        f"**/api/v1/workspaces/*/members/test-member/permissions/notes.create",
        lambda route: (
            requests.append(route.request.method),
            route.fulfill(status=204),
        )[-1],
    )
    select = page.locator("[data-permission-override]").last

    select.select_option(next_value)

    expect(page.locator("[data-permission-status]")).to_have_text(
        "권한 재정의를 저장했습니다."
    )
    expect(select).to_have_attribute("data-previous", next_value)
    assert requests == [expected_method]


def test_workspace_permission_override_restores_prior_value_after_failure(
    page: Page, live_server: LiveServer
) -> None:
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/permissions")
    _insert_mutable_permission_override(page, live_server.workspace_id, "DENY")
    page.route(
        f"**/api/v1/workspaces/*/members/test-member/permissions/notes.create",
        lambda route: route.fulfill(status=500, body="failure"),
    )
    select = page.locator("[data-permission-override]").last

    select.select_option("ALLOW")

    expect(page.locator("[data-permission-status]")).to_have_text(
        "요청을 처리하지 못했습니다. 다시 시도해 주세요."
    )
    expect(select).to_have_value("DENY")
    expect(select).to_be_enabled()


def test_workspace_settings_contains_hostile_long_values_without_page_overflow(
    page: Page, live_server: LiveServer
) -> None:
    page.set_viewport_size({"width": 320, "height": 800})
    page.goto(f"{live_server}/w/{live_server.workspace_id}/settings/permissions")
    page.evaluate(
        """value => {
          document.querySelector('.workspace-settings-hero h2').textContent = value;
          document.querySelector('.permission-table td strong').textContent = value;
          document.querySelector('.permission-table code').textContent = value;
        }""",
        "LONG_UNBROKEN_VALUE_" * 32,
    )

    metrics = page.evaluate(
        """() => {
          const root = document.querySelector('[data-workspace-settings]');
          const matrix = document.querySelector('[data-permission-matrix]');
          return {
            bodyWidth: document.body.scrollWidth,
            viewportWidth: document.documentElement.clientWidth,
            rootWidth: root.scrollWidth,
            rootClientWidth: root.clientWidth,
            matrixOwnsOverflow: matrix.scrollWidth >= matrix.clientWidth,
          };
        }"""
    )
    assert metrics["bodyWidth"] == metrics["viewportWidth"]
    assert metrics["rootWidth"] == metrics["rootClientWidth"]
    assert metrics["matrixOwnsOverflow"] is True


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
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{live_server}/hub")

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
    page.on(
        "requestfailed",
        lambda request: failed_requests.append(
            f"{request.method} {request.failure} {request.url}"
        ),
    )

    page.goto(f"{live_server}/apps/calendar/")
    expect(page.locator("#sidebar")).to_be_attached()
    expect(page.locator(".workspace-header")).to_be_visible()
    expect(page.locator('[data-app-id="calendar"][aria-current="page"]')).to_be_attached()
    expect(page.locator("#calendar")).to_be_visible()
    expect(page.locator("#create-menu-button")).to_be_visible()
    expect(page.locator('.workspace-navigation a').filter(has_text="Home")).to_have_attribute(
        "href", f"/w/{live_server.workspace_id}/home"
    )
    assert failed_requests == []
    assert external_requests == []


def test_shared_loader_waits_for_calendar_data_before_dismissing(
    page: Page, live_server: str
) -> None:
    pending_routes = []
    tasks_url = (
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/calendar/tasks"
    )
    page.route(tasks_url, lambda route: pending_routes.append(route))

    page.goto(
        f"{live_server}/w/{live_server.workspace_id}/calendar",
        wait_until="domcontentloaded",
    )

    loader = page.locator("#workspace-app-loading")
    expect(loader).to_be_visible()
    expect(page.locator("#workspace-main")).to_have_attribute("aria-busy", "true")
    deadline = time.monotonic() + 5
    while not pending_routes and time.monotonic() < deadline:
        page.wait_for_timeout(20)
    assert len(pending_routes) == 1
    pending_routes.pop().fulfill(status=200, json=[])
    expect(loader).to_be_hidden()
    expect(page.locator("#workspace-main")).to_have_attribute("aria-busy", "false")


def test_calendar_click_visibly_highlights_only_the_selected_date(
    page: Page, live_server: str
) -> None:
    page_errors: list[str] = []
    console_errors: list[str] = []
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on(
        "console",
        lambda message: console_errors.append(message.text)
        if message.type == "error"
        else None,
    )
    page.goto(f"{live_server}/apps/calendar/")
    days = page.locator('[role="gridcell"][data-date]')
    first = days.nth(0)
    second = days.nth(1)

    first.click()
    expect(first).to_have_class(re.compile(r"\bis-selected-date\b"))
    expect(first).to_have_css("box-shadow", re.compile(r"inset"))

    second.click()
    expect(second).to_have_class(re.compile(r"\bis-selected-date\b"))
    expect(first).not_to_have_class(re.compile(r"\bis-selected-date\b"))
    assert page_errors == []
    assert console_errors == []


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
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/calendar/tasks",
        headers=csrf_headers(live_server),
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
    detail = dialog.locator("#task-detail-content")
    viewer = dialog.locator("#task-description-viewer")
    expect(detail).to_be_visible()
    expect(dialog.locator("#task-edit-content")).to_be_hidden()
    expect(viewer.locator("strong")).to_have_text("중요 일정")
    expect(viewer.locator("h2")).to_have_text("준비 사항")
    expect(viewer.locator("script")).to_have_count(0)
    assert page.evaluate("window.__calendarMarkdownXss") is None

    dialog.get_by_role("button", name="편집").click()
    expect(dialog.locator("#task-edit-content")).to_be_visible()
    expect(detail).to_be_hidden()
    dialog.locator("#task-description").press("End")
    dialog.locator("#task-description").type("\n\n수정됨")
    dialog.get_by_role("button", name="저장").click()
    expect(detail).to_be_visible()
    expect(viewer).to_contain_text("수정됨")
    expect(dialog.get_by_role("button", name="편집")).to_be_focused()

    dialog.get_by_role("button", name="편집").click()
    dialog.locator("#task-title").fill("저장하지 않을 제목")
    page.once("dialog", lambda prompt: prompt.accept())
    dialog.get_by_role("button", name="취소").click()
    expect(detail).to_be_visible()
    expect(dialog.locator("#task-detail-title")).to_have_text("Markdown 일정")
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


def test_calendar_oversized_markdown_uses_safe_detail_fallback(
    page: Page,
    live_server: str,
) -> None:
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: (
            console_errors.append(message.text) if message.type == "error" else None
        ),
    )
    oversized = f"{'x' * 200_001}<script>window.__oversizedXss = true</script>"
    response = page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/calendar/tasks",
        headers=csrf_headers(live_server),
        data={
            "title": "대용량 Markdown 일정",
            "description": oversized,
            "status": "todo",
            "all_day": False,
            "start_at": "2026-07-28T11:00:00+09:00",
            "due_at": "2026-07-28T12:00:00+09:00",
            "topic_ids": [],
        },
    )
    assert response.ok

    page.goto(f"{live_server}/apps/calendar/")
    page.get_by_role("button", name="일정 열기: 대용량 Markdown 일정").click()
    viewer = page.locator("#task-description-viewer")
    expect(viewer.locator(".markdown-source-fallback")).to_be_visible()
    expect(viewer.locator("script")).to_have_count(0)
    expect(viewer).to_contain_text(
        "<script>window.__oversizedXss = true</script>"
    )
    assert page.evaluate("window.__oversizedXss") is None
    assert console_errors == []


def test_notes_registry_surfaces_and_crud(page: Page, live_server: str) -> None:
    external_requests = assert_same_origin_requests(page, live_server)
    failed_requests: list[str] = []
    http_errors: list[str] = []
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
    page.on(
        "response",
        lambda response: http_errors.append(
            f"{response.status} {response.request.method} {response.url}"
        ) if response.status >= 400 else None,
    )
    page.goto(f"{live_server}/hub")
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
    assert http_errors == []
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


def test_notes_clipboard_image_with_korean_name_round_trips(
    page: Page,
    live_server: str,
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
    page.goto(f"{live_server}/apps/notes/")
    page.get_by_role("button", name="새 메모", exact=True).click()
    page.locator("#note-title").fill("클립보드 이미지")
    content = page.locator("#note-content")
    content.focus()
    content.evaluate(
        """(textarea) => {
          const encoded = "iVBORw0KGgoAAAANSUhEUgAAAAMAAAACCAIAAAASFvFNAAAAFUlEQVR4nGMU0bBhYGBgYGBgYoABAAiEAHxweBEFAAAAAElFTkSuQmCC";
          const bytes = Uint8Array.from(atob(encoded), (value) => value.charCodeAt(0));
          const file = new File([bytes], "복사한 이미지.png", { type: "image/png" });
          const transfer = new DataTransfer();
          transfer.items.add(file);
          const event = new ClipboardEvent("paste", {
            bubbles: true,
            cancelable: true,
          });
          Object.defineProperty(event, "clipboardData", { value: transfer });
          textarea.dispatchEvent(event);
        }"""
    )
    expect(content).to_have_value(re.compile(r"!\[복사한 이미지\]\(image://\d+\)"))
    page.get_by_role("button", name="저장").click()
    expect(page.locator("#notes-announcer")).to_contain_text("저장")
    image = page.locator("#note-view-content img")
    expect(image).to_have_attribute("alt", "복사한 이미지")
    expect(image).to_have_attribute(
        "src",
        re.compile(
            rf"/api/v1/workspaces/{re.escape(live_server.workspace_id)}/assets/\d+/content"
        ),
    )
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


def test_note_publish_flow_creates_public_manual_snapshot(
    page: Page,
    live_server: str,
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
    note = page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/notes",
        headers=csrf_headers(live_server),
        data={
            "title": "Publishing E2E",
            "content": "## Immutable\n\nPublished snapshot",
            "note_type": "general",
        },
    ).json()
    page.goto(f"{live_server}/apps/notes/?note_id={note['id']}")
    page.get_by_role("button", name="발행").click()
    dialog = page.locator("#publish-dialog")
    expect(dialog).to_be_visible()
    dialog.locator("#publication-type").select_option("manual")
    dialog.locator("#publication-slug").fill("publishing-e2e")
    dialog.locator("#publication-summary").fill("E2E publication")
    dialog.get_by_role("button", name="미리보기").click()
    expect(dialog.locator("#publication-preview h2")).to_have_text("Immutable")
    dialog.get_by_role("button", name="Draft 저장").click()
    expect(dialog.locator("#publication-status")).to_contain_text("Draft v1")
    expect(dialog.get_by_role("button", name="Publish")).to_be_enabled()
    dialog.get_by_role("button", name="Publish").click()
    expect(dialog).to_be_hidden()

    published = page.request.get(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/publications",
        params={"status": "published"},
    ).json()
    publication = next(item for item in published if item["slug"] == "publishing-e2e")
    public_url = f"{live_server}/p/{publication['public_id']}/publishing-e2e"
    page.goto(public_url)
    expect(page.get_by_role("heading", name="Publishing E2E")).to_be_visible()
    expect(page.locator("#publication-markdown h2")).to_have_text("Immutable")
    expect(page.locator("#publication-markdown")).to_contain_text(
        "Published snapshot"
    )
    page.once("dialog", lambda prompt: prompt.accept())
    page.get_by_role("button", name="발행물 삭제").click()
    expect(page).to_have_url(re.compile(r"/publications/$"))
    expect(page.locator(".publication-card").filter(has_text="Publishing E2E")).to_have_count(0)
    archived = page.request.get(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/publications",
        params={"status": "archived"},
    ).json()
    assert [item["slug"] for item in archived] == ["publishing-e2e"]
    assert page.request.get(public_url).status == 404
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


def test_long_publication_scrolls_inside_workspace_main(
    page: Page,
    live_server: str,
) -> None:
    markdown = "\n\n".join(
        f"## Section {index}\n\n"
        + "Long publication content " * 20
        for index in range(1, 31)
    )
    note = page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/notes",
        headers=csrf_headers(live_server),
        data={
            "title": "Long Publication",
            "content": markdown,
            "note_type": "general",
        },
    ).json()
    publication = page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/publications",
        headers=csrf_headers(live_server),
        data={
            "note_id": note["id"],
            "type_id": "manual",
            "slug": "long-publication",
            "summary": "Scroll ownership regression",
        },
    ).json()
    assert page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/publications/{publication['id']}/publish",
        headers=csrf_headers(live_server),
    ).ok

    page.set_viewport_size({"width": 375, "height": 667})
    page.goto(
        f"{live_server}/p/{publication['public_id']}/long-publication"
    )
    main = page.locator("#workspace-main")
    expect(page.get_by_role("heading", name="Long Publication")).to_be_visible()
    before = main.evaluate(
        """(element) => ({
          clientHeight: element.clientHeight,
          scrollHeight: element.scrollHeight,
          overflowY: getComputedStyle(element).overflowY,
          bodyHeight: document.body.scrollHeight,
          viewportHeight: document.body.clientHeight,
          canvasBackground: getComputedStyle(element).backgroundColor,
          readingBackground: getComputedStyle(
            document.querySelector('.publication-markdown')
          ).backgroundColor,
        })"""
    )
    assert before["overflowY"] == "auto"
    assert before["scrollHeight"] > before["clientHeight"] * 2
    assert before["bodyHeight"] == before["viewportHeight"]
    assert before["readingBackground"] != before["canvasBackground"]

    main.evaluate("(element) => element.scrollTo({ top: element.scrollHeight })")
    page.wait_for_timeout(100)
    assert main.evaluate("(element) => element.scrollTop") > 0
    expect(page.get_by_role("heading", name="Section 30")).to_be_visible()


def test_publication_type_management_ui_crud(
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
    page.goto(
        f"{live_server}/w/{live_server.workspace_id}/publications/types"
    )
    expect(page.get_by_role("heading", name="Publication Type 관리")).to_be_visible()
    expect(page.locator("#publication-type-list")).to_contain_text("Manual")
    expect(page.locator("#publication-type-list")).to_contain_text("Wiki")
    page.locator("#type-id").fill("faq")
    page.locator("#type-display-name").fill("FAQ")
    page.locator("#type-order").fill("30")
    page.locator("#type-related").check()
    page.get_by_role("button", name="저장").click()
    expect(page.locator("#publication-type-status")).to_contain_text(
        "즉시 적용"
    )
    faq_card = page.locator(".publication-type-card").filter(has_text="FAQ")
    expect(faq_card).to_contain_text("Enabled")
    faq_card.get_by_role("button", name="수정").click()
    page.once("dialog", lambda prompt: prompt.accept())
    page.get_by_role("button", name="삭제").click()
    expect(page.locator("#publication-type-status")).to_contain_text("삭제")
    expect(page.locator(".publication-type-card").filter(has_text="FAQ")).to_have_count(0)
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


@pytest.mark.parametrize(("width", "height"), VIEWPORTS)
def test_publication_surfaces_viewport_layout_and_request_health(
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
    page.goto(f"{live_server}/publications/")
    expect(
        page.get_by_role("heading", name="발행된 지식을 한곳에서 읽으세요")
    ).to_be_visible()
    home_metrics = page.evaluate(
        """() => ({
          innerWidth: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          pageWidth: document.querySelector('.publication-home').getBoundingClientRect().width,
          mainWidth: document.querySelector('#workspace-main').getBoundingClientRect().width,
        })"""
    )
    assert home_metrics["documentWidth"] == home_metrics["innerWidth"]
    assert 0 < home_metrics["pageWidth"] <= home_metrics["mainWidth"]

    page.goto(f"{live_server}/publications/types/")
    page.wait_for_timeout(150)
    metrics = page.evaluate(
        """() => ({
          innerWidth: window.innerWidth,
          documentWidth: document.documentElement.scrollWidth,
          pageWidth: document.querySelector('.publication-types-page').getBoundingClientRect().width,
          mainWidth: document.querySelector('#workspace-main').getBoundingClientRect().width,
        })"""
    )
    assert metrics["documentWidth"] == metrics["innerWidth"]
    assert 0 < metrics["pageWidth"] <= metrics["mainWidth"]
    assert external_requests == []
    assert failed_requests == []
    assert console_errors == []


def test_calendar_creates_linked_meeting_note(page: Page, live_server: str) -> None:
    task = page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/calendar/tasks",
        headers=csrf_headers(live_server),
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
    expect(page).to_have_url(re.compile(r"/w/[^/]+/notes/new\?.*type=meeting_minutes"))
    expect(page.locator("#note-context")).to_contain_text(f"일정 #{task['id']}")
    page.locator("#note-title").fill("Notes 연계 회의록")
    page.get_by_role("button", name="저장").click()
    expect(page.locator("#view-note-context")).to_contain_text("Notes 연계 회의")

    linked = page.request.get(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/notes",
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
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/notes",
        headers=csrf_headers(live_server),
        data={"title": "기존 연결 노트", "content": "", "note_type": "general"},
    ).json()
    task = page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/calendar/tasks",
        headers=csrf_headers(live_server),
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
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/notes/{note['id']}/links"
    ).json() == []
    assert page.request.get(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/calendar/tasks"
    ).json()
    assert task["id"] > 0


def test_deleted_calendar_event_leaves_orphaned_note(
    page: Page, live_server: str
) -> None:
    task = page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/calendar/tasks",
        headers=csrf_headers(live_server),
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
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/notes",
        headers=csrf_headers(live_server),
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
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/calendar/tasks/{task['id']}",
        headers=csrf_headers(live_server),
    ).status == 204

    page.goto(f"{live_server}/apps/notes/?note_id={note['id']}")
    expect(page.locator("#note-view-title")).to_have_text("보존되는 노트")
    expect(page.locator("#view-note-context")).to_contain_text("삭제 후 snapshot 일정")
    expect(page.locator("#view-note-context")).to_contain_text("삭제된 일정")


def test_notes_browser_back_restores_list_and_focus(
    page: Page, live_server: str
) -> None:
    note = page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/notes",
        headers=csrf_headers(live_server),
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
    expect(page).to_have_url(re.compile(r"/w/[^/]+/notes$"))
    expect(page.locator("#notes-empty")).to_be_visible()
    expect(page.locator("#note-form")).to_be_hidden()
    expect(page.locator("#notes-search")).to_be_focused()

    page.locator("#new-note").click()
    expect(page).to_have_url(re.compile(r"/w/[^/]+/notes/new$"))
    expect(page.locator("#note-form")).to_be_visible()
    expect(page.locator("#note-view")).to_be_hidden()

    page.go_back()
    expect(page).to_have_url(re.compile(r"/w/[^/]+/notes$"))
    expect(page.locator("#notes-empty")).to_be_visible()

    page.go_forward()
    expect(page).to_have_url(re.compile(r"/w/[^/]+/notes/new$"))
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
    current_month = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m")
    page.request.post(
        f"{live_server}/api/v1/workspaces/{live_server.workspace_id}/calendar/tasks",
        headers=csrf_headers(live_server),
        data={
            "title": title,
            "description": "",
            "status": "todo",
            "all_day": False,
            "start_at": f"{current_month}-{day:02d}T15:00:00+09:00",
            "due_at": f"{current_month}-{day:02d}T16:00:00+09:00",
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
