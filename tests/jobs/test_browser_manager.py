from __future__ import annotations

import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import urlsplit

import pytest

from job_platform.errors import JobValidationError
from web_collector.browser import BrowserManager, BrowserOperationError, ResolvedTarget


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/controls":
            body = b"""<!doctype html><title>Controls</title>
            <button id='popup'>Popup</button><button id='download'>Download</button>
            <script>
              popup.onclick=()=>window.open('/popup','fixture-popup');
              download.onclick=()=>location.href='/download';
            </script>"""
        elif self.path == "/popup":
            body = b"<!doctype html><title>Popup</title><p>popup</p>"
        elif self.path == "/download":
            body = b"blocked download payload"
        elif self.path == "/large":
            body = b"<main id='target'>" + (b"x" * 200_000) + b"</main>"
        else:
            body = b"""<!doctype html><title>Fixture</title>
            <main id='target' class='generated-layout' data-runtime='large'>
              <h1>Notice</h1><p>Body</p><svg><path d='large-payload'/></svg>
              <a href='/detail' class='link'>Detail</a>
            </main>
            <div class='duplicate'>A</div><div class='duplicate'>B</div>
            <script src='/allowed.js'></script>
            <script src='/blocked.js'></script>"""
        if self.path == "/allowed.js":
            body = b"document.querySelector('#target p').textContent='Loading';setTimeout(()=>document.querySelector('#target p').textContent='Rendered',900);"
        elif self.path == "/blocked.js":
            body = b"document.querySelector('#target p').textContent = 'Unsafe';"
        self.send_response(200)
        content_type = (
            "application/javascript; charset=utf-8"
            if self.path.endswith(".js")
            else "text/html; charset=utf-8"
        )
        self.send_header("Content-Type", content_type)
        if self.path == "/download":
            self.send_header("Content-Disposition", "attachment; filename=fixture.txt")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


@contextmanager
def fixture_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()


class FixturePolicy:
    def validate(self, value: str) -> ResolvedTarget:
        parsed = urlsplit(value)
        if parsed.path == "/blocked.js":
            raise JobValidationError("blocked test subresource")
        return ResolvedTarget(value, parsed.hostname or "", parsed.port or 80, ("127.0.0.1",))


def test_browser_collects_rendered_unique_element_and_blocks_subresource(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", ".playwright-browsers")

    async def scenario(base_url):
        manager = BrowserManager(url_policy=FixturePolicy())
        try:
            result = await manager.collect(f"{base_url}/", "#target")
            assert result.page_title == "Fixture"
            assert "Rendered" in result.content_html
            assert "Unsafe" not in result.content_html
            assert "<svg" not in result.content_html
            assert "generated-layout" not in result.content_html
            assert 'href="/detail"' in result.content_html
            assert " ".join(result.content_text.split()) == "Notice Rendered Detail"
            assert manager.available is True
        finally:
            await manager.close()
            await manager.close()
        assert manager.available is False

    with fixture_server() as base_url:
        asyncio.run(scenario(base_url))


def test_browser_preview_returns_bounded_jpeg_and_unique_hit_map(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", ".playwright-browsers")

    async def scenario(base_url):
        manager = BrowserManager(url_policy=FixturePolicy())
        try:
            result = await manager.preview(f"{base_url}/")
            assert result.width <= 1920
            assert result.height <= 5000
            assert len(result.screenshot_base64) < 3 * 1024 * 1024
            target = next(
                item for item in result.candidates if item["selector"] == "#target"
            )
            assert target["tag"] == "main"
            assert "Rendered" in target["text"]
            assert target["width"] > 0 and target["height"] > 0
        finally:
            await manager.close()

    with fixture_server() as base_url:
        asyncio.run(scenario(base_url))


@pytest.mark.parametrize(
    ("selector", "code"),
    [("#missing", "SELECTOR_NOT_FOUND"), (".duplicate", "SELECTOR_NOT_UNIQUE")],
)
def test_browser_rejects_missing_or_ambiguous_selector(monkeypatch, selector, code):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", ".playwright-browsers")

    async def scenario(base_url):
        manager = BrowserManager(url_policy=FixturePolicy())
        try:
            with pytest.raises(BrowserOperationError, match=code):
                await manager.collect(f"{base_url}/", selector)
        finally:
            await manager.close()

    with fixture_server() as base_url:
        asyncio.run(scenario(base_url))


def test_browser_aborts_encoded_transfer_over_budget(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", ".playwright-browsers")

    async def scenario(base_url):
        manager = BrowserManager(
            url_policy=FixturePolicy(), transfer_limit_bytes=10_000
        )
        try:
            with pytest.raises(BrowserOperationError, match="TRANSFER_LIMIT_EXCEEDED"):
                await manager.collect(f"{base_url}/large", "#target")
        finally:
            await manager.close()

    with fixture_server() as base_url:
        asyncio.run(scenario(base_url))


def test_browser_preview_uses_bounded_preview_budget_for_heavy_pages(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", ".playwright-browsers")

    async def scenario(base_url):
        manager = BrowserManager(
            url_policy=FixturePolicy(),
            transfer_limit_bytes=10_000,
            preview_transfer_limit_bytes=400_000,
        )
        try:
            preview = await manager.preview(f"{base_url}/large")
            assert preview.width > 0
            with pytest.raises(BrowserOperationError, match="TRANSFER_LIMIT_EXCEEDED"):
                await manager.collect(f"{base_url}/large", "#target")
        finally:
            await manager.close()

    with fixture_server() as base_url:
        asyncio.run(scenario(base_url))


def test_browser_rejects_concurrency_saturation_without_starting_chromium():
    async def scenario():
        manager = BrowserManager(
            url_policy=FixturePolicy(), concurrency=1, acquire_timeout_seconds=0.01
        )
        await manager._semaphore.acquire()
        try:
            with pytest.raises(BrowserOperationError, match="BROWSER_SATURATED"):
                await manager.collect("http://example.test/", "main")
        finally:
            manager._semaphore.release()

    asyncio.run(scenario())


def test_browser_crash_is_bounded_and_resets_runtime():
    class TargetClosedError(RuntimeError):
        pass

    class CrashedBrowser:
        def is_connected(self):
            return False

        async def new_context(self, **_options):
            raise TargetClosedError("browser target closed")

        async def close(self):
            raise TargetClosedError("already closed")

    class CrashedPlaywright:
        async def stop(self):
            raise TargetClosedError("already stopped")

    async def scenario():
        manager = BrowserManager(url_policy=FixturePolicy())
        manager._browser = CrashedBrowser()
        manager._playwright = CrashedPlaywright()

        async def keep_crashed_runtime():
            return None

        manager.start = keep_crashed_runtime
        with pytest.raises(BrowserOperationError, match="BROWSER_CRASHED"):
            await manager.collect("http://example.test/", "main")
        assert manager._browser is None
        assert manager._playwright is None
        assert manager.available is False

    asyncio.run(scenario())


def test_popup_download_and_repeated_contexts_are_closed(monkeypatch):
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", ".playwright-browsers")

    async def scenario(base_url):
        manager = BrowserManager(url_policy=FixturePolicy())

        async def exercise_controls(page, _target):
            await page.locator("#popup").click()
            await page.wait_for_timeout(250)
            assert len(page.context.pages) == 1
            async with page.expect_download(timeout=2_000) as pending:
                await page.locator("#download").click()
            download = await pending.value
            await page.wait_for_timeout(100)
            assert download.suggested_filename == "fixture.txt"
            assert await download.failure() is not None
            return True

        async def read_title(page, _target):
            return await page.title()

        try:
            assert await manager._run(f"{base_url}/controls", exercise_controls)
            assert len(manager._browser.contexts) == 0
            for _ in range(20):
                assert await manager._run(f"{base_url}/", read_title) == "Fixture"
                assert len(manager._browser.contexts) == 0
        finally:
            await manager.close()
        assert manager.available is False

    with fixture_server() as base_url:
        asyncio.run(scenario(base_url))
