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
        if self.path == "/large":
            body = b"<main id='target'>" + (b"x" * 200_000) + b"</main>"
        else:
            body = b"""<!doctype html><title>Fixture</title>
            <main id='target'><h1>Notice</h1><p>Body</p></main>
            <div class='duplicate'>A</div><div class='duplicate'>B</div>
            <script src='/allowed.js'></script>
            <script src='/blocked.js'></script>"""
        if self.path == "/allowed.js":
            body = b"document.querySelector('#target p').textContent = 'Rendered';"
        elif self.path == "/blocked.js":
            body = b"document.querySelector('#target p').textContent = 'Unsafe';"
        self.send_response(200)
        content_type = (
            "application/javascript; charset=utf-8"
            if self.path.endswith(".js")
            else "text/html; charset=utf-8"
        )
        self.send_header("Content-Type", content_type)
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
            assert result.content_text == "Notice\n\nRendered"
            assert manager.available is True
        finally:
            await manager.close()
            await manager.close()
        assert manager.available is False

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
