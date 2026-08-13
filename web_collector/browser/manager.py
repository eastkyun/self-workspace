from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

from job_platform.errors import JobValidationError

from .url_policy import ResolvedTarget, UrlPolicy


class TargetPolicy(Protocol):
    def validate(self, value: str) -> ResolvedTarget: ...


@dataclass(frozen=True, slots=True)
class BrowserCollection:
    final_url: str
    content_html: str
    content_text: str
    page_title: str


class BrowserOperationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class BrowserManager:
    """One Chromium process with an isolated context for every operation."""

    def __init__(
        self,
        *,
        url_policy: TargetPolicy | None = None,
        concurrency: int = 2,
        acquire_timeout_seconds: float = 1,
        navigation_timeout_ms: int = 10_000,
        operation_timeout_seconds: float = 20,
        transfer_limit_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        if concurrency < 1 or transfer_limit_bytes < 1:
            raise ValueError("browser limits must be positive")
        self.url_policy = url_policy or UrlPolicy()
        self._semaphore = asyncio.Semaphore(concurrency)
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.navigation_timeout_ms = navigation_timeout_ms
        self.operation_timeout_seconds = operation_timeout_seconds
        self.transfer_limit_bytes = transfer_limit_bytes
        self._playwright = None
        self._browser = None
        self._start_lock = asyncio.Lock()

    @property
    def available(self) -> bool:
        return self._browser is not None and self._browser.is_connected()

    async def start(self) -> None:
        async with self._start_lock:
            if self.available:
                return
            try:
                from playwright.async_api import async_playwright

                self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.launch(headless=True)
            except Exception as error:
                await self.close()
                raise BrowserOperationError("BROWSER_UNAVAILABLE") from error

    async def close(self) -> None:
        browser, playwright = self._browser, self._playwright
        self._browser = self._playwright = None
        if browser is not None:
            await browser.close()
        if playwright is not None:
            await playwright.stop()

    async def collect(self, url: str, selector: str) -> BrowserCollection:
        if not 1 <= len(selector) <= 512:
            raise JobValidationError("selector must contain 1-512 characters")
        initial = self.url_policy.validate(url)
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self.acquire_timeout_seconds
            )
        except TimeoutError as error:
            raise BrowserOperationError("BROWSER_SATURATED") from error
        try:
            return await asyncio.wait_for(
                self._collect(initial.url, selector),
                timeout=self.operation_timeout_seconds,
            )
        except TimeoutError as error:
            raise BrowserOperationError("BROWSER_TIMEOUT") from error
        finally:
            self._semaphore.release()

    async def _collect(self, url: str, selector: str) -> BrowserCollection:
        await self.start()
        context = await self._browser.new_context(
            accept_downloads=False,
            service_workers="block",
            viewport={"width": 1280, "height": 900},
            device_scale_factor=1,
        )
        page = await context.new_page()
        transferred = 0
        request_transfers: dict[str, int] = {}
        transfer_exceeded = asyncio.Event()
        overflow_task = None
        cdp = None

        async def route_request(route) -> None:
            request = route.request
            if request.resource_type in {"websocket", "eventsource"}:
                await route.abort("blockedbyclient")
                return
            try:
                self.url_policy.validate(request.url)
            except JobValidationError:
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        async def close_popup(popup) -> None:
            await popup.close()

        def count_transfer(event) -> None:
            nonlocal transferred, overflow_task
            request_id = str(event.get("requestId") or "")
            size = int(event.get("encodedDataLength") or event.get("dataLength") or 0)
            request_transfers[request_id] = request_transfers.get(request_id, 0) + size
            transferred += size
            if transferred > self.transfer_limit_bytes and not transfer_exceeded.is_set():
                transfer_exceeded.set()
                overflow_task = asyncio.create_task(page.close())

        def finish_transfer(event) -> None:
            nonlocal transferred, overflow_task
            request_id = str(event.get("requestId") or "")
            encoded = int(event.get("encodedDataLength") or 0)
            counted = request_transfers.get(request_id, 0)
            if encoded > counted:
                transferred += encoded - counted
                request_transfers[request_id] = encoded
            if transferred > self.transfer_limit_bytes and not transfer_exceeded.is_set():
                transfer_exceeded.set()
                overflow_task = asyncio.create_task(page.close())

        try:
            await context.route("**/*", route_request)
            page.on("popup", close_popup)
            cdp = await context.new_cdp_session(page)
            cdp.on("Network.dataReceived", count_transfer)
            cdp.on("Network.loadingFinished", finish_transfer)
            await cdp.send("Network.enable")
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )
            if transfer_exceeded.is_set():
                raise BrowserOperationError("TRANSFER_LIMIT_EXCEEDED")
            final_target = self.url_policy.validate(page.url)
            locator = page.locator(selector)
            count = await locator.count()
            if count == 0:
                raise BrowserOperationError("SELECTOR_NOT_FOUND")
            if count != 1:
                raise BrowserOperationError("SELECTOR_NOT_UNIQUE")
            element = locator.first
            await element.wait_for(state="attached", timeout=2_000)
            html = await element.evaluate("element => element.outerHTML")
            text = await element.inner_text()
            if len(html) > 1_000_000 or len(text) > 100_000:
                raise BrowserOperationError("EXTRACTED_CONTENT_TOO_LARGE")
            return BrowserCollection(
                final_url=final_target.url,
                content_html=html,
                content_text=text,
                page_title=(await page.title())[:120],
            )
        except BrowserOperationError:
            raise
        except Exception as error:
            if transfer_exceeded.is_set():
                raise BrowserOperationError("TRANSFER_LIMIT_EXCEEDED") from error
            raise BrowserOperationError("BROWSER_NAVIGATION_FAILED") from error
        finally:
            if overflow_task is not None:
                await asyncio.gather(overflow_task, return_exceptions=True)
            if cdp is not None:
                try:
                    await cdp.detach()
                except Exception:
                    pass
            await context.close()
