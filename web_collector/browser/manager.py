from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol, TypeVar

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


@dataclass(frozen=True, slots=True)
class BrowserPreview:
    final_url: str
    page_title: str
    screenshot_base64: str
    width: int
    height: int
    truncated_x: bool
    truncated_y: bool
    candidates: tuple[dict[str, Any], ...]


class BrowserOperationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


T = TypeVar("T")
PageOperation = Callable[[Any, ResolvedTarget], Awaitable[T]]


_CLEAN_HTML_SCRIPT = r"""
element => {
  const clone = element.cloneNode(true);
  clone.querySelectorAll("script,style,noscript,template,form,svg,canvas,iframe,video,audio,source,picture,link,meta").forEach(node => node.remove());
  const allowed = new Set(["href", "title", "alt", "colspan", "rowspan", "scope", "datetime", "cite"]);
  for (const node of [clone, ...clone.querySelectorAll("*")]) {
    for (const attribute of [...node.attributes]) {
      if (!allowed.has(attribute.name.toLowerCase())) node.removeAttribute(attribute.name);
    }
    if (node.tagName === "IMG") {
      node.removeAttribute("src");
      node.removeAttribute("srcset");
    }
  }
  return clone.outerHTML;
}
"""


_PAGE_SETTLE_SCRIPT = r"""
async () => {
  const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const started = Date.now();
  let previous = "";
  let stable = 0;
  while (Date.now() - started < 6000) {
    const root = document.documentElement;
    const fingerprint = [
      (document.body?.innerText || "").replace(/\s+/g, " ").trim().slice(0, 12000),
      root.scrollWidth,
      root.scrollHeight,
      document.querySelectorAll("main,article,section,table,[role]").length,
    ].join("|");
    stable = fingerprint === previous ? stable + 1 : 0;
    previous = fingerprint;
    if (stable >= 3 && Date.now() - started >= 1500) break;
    await sleep(250);
  }
  scrollTo(0, 0);
}
"""


_ELEMENT_SETTLE_SCRIPT = r"""
async element => {
  const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
  const started = Date.now();
  let previous = "";
  let stable = 0;
  while (Date.now() - started < 2500) {
    const rect = element.getBoundingClientRect();
    const fingerprint = [
      (element.innerText || "").replace(/\s+/g, " ").trim().slice(0, 12000),
      element.childElementCount,
      Math.round(rect.width),
      Math.round(rect.height),
    ].join("|");
    stable = fingerprint === previous ? stable + 1 : 0;
    previous = fingerprint;
    if (stable >= 3 && Date.now() - started >= 1000) break;
    await sleep(250);
  }
}
"""


_HIT_MAP_SCRIPT = r"""
() => {
  const stable = (value) => value && value.length <= 64 &&
    !/[0-9a-f]{8,}/i.test(value) && !/^\d+$/.test(value);
  const unique = (selector) => {
    try { return document.querySelectorAll(selector).length === 1; }
    catch (_) { return false; }
  };
  const selectorFor = (element) => {
    if (stable(element.id)) {
      const selector = `#${CSS.escape(element.id)}`;
      if (unique(selector)) return selector;
    }
    for (const name of ["data-testid", "data-test", "data-qa", "data-cy", "data-id", "data-key", "data-section", "data-component", "name", "role", "aria-label", "itemprop"]) {
      const value = element.getAttribute(name);
      if (!stable(value)) continue;
      const selector = `${element.tagName.toLowerCase()}[${name}="${CSS.escape(value)}"]`;
      if (unique(selector)) return selector;
    }
    const classes = [...element.classList].filter(stable).slice(0, 3);
    if (classes.length) {
      const selector = element.tagName.toLowerCase() + classes.map(value => `.${CSS.escape(value)}`).join("");
      if (unique(selector)) return selector;
    }
    const parts = [];
    let current = element;
    while (current && current !== document.body && parts.length < 5) {
      const tag = current.tagName.toLowerCase();
      const siblings = current.parentElement ? [...current.parentElement.children].filter(item => item.tagName === current.tagName) : [];
      const position = siblings.length > 1 ? `:nth-of-type(${siblings.indexOf(current) + 1})` : "";
      parts.unshift(tag + position);
      const selector = parts.join(" > ");
      if (unique(selector)) return selector;
      current = current.parentElement;
    }
    return null;
  };
  const query = "main,article,section,nav,header,footer,aside,h1,h2,h3,table,ul,ol,pre,blockquote,[id],[data-testid],[data-test],[data-qa],[data-cy],[data-section],[role]";
  const seen = new Set();
  const candidates = [];
  for (const element of document.querySelectorAll(query)) {
    if (candidates.length >= 1000 || seen.has(element)) continue;
    seen.add(element);
    const rect = element.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) continue;
    const selector = selectorFor(element);
    if (!selector || selector.length > 512) continue;
    const x = Math.max(0, rect.left + scrollX);
    const y = Math.max(0, rect.top + scrollY);
    if (x >= 1920 || y >= 3600) continue;
    candidates.push({
      selector,
      tag: element.tagName.toLowerCase(),
      text: (element.innerText || "").replace(/\s+/g, " ").trim().slice(0, 160),
      x, y,
      width: Math.min(rect.width, 1920 - x),
      height: Math.min(rect.height, 3600 - y)
    });
  }
  const root = document.documentElement;
  return {
    width: Math.max(root.scrollWidth, document.body?.scrollWidth || 0),
    height: Math.max(root.scrollHeight, document.body?.scrollHeight || 0),
    candidates
  };
}
"""


class BrowserManager:
    """One Chromium process with an isolated context for every operation."""

    def __init__(
        self,
        *,
        url_policy: TargetPolicy | None = None,
        concurrency: int = 2,
        acquire_timeout_seconds: float = 15,
        navigation_timeout_ms: int = 10_000,
        operation_timeout_seconds: float = 20,
        preview_operation_timeout_seconds: float = 40,
        transfer_limit_bytes: int = 96 * 1024 * 1024,
        preview_transfer_limit_bytes: int = 96 * 1024 * 1024,
    ) -> None:
        if (
            concurrency < 1
            or operation_timeout_seconds <= 0
            or preview_operation_timeout_seconds <= 0
            or transfer_limit_bytes < 1
            or preview_transfer_limit_bytes < 1
        ):
            raise ValueError("browser limits must be positive")
        self.url_policy = url_policy or UrlPolicy()
        self._semaphore = asyncio.Semaphore(concurrency)
        self.acquire_timeout_seconds = acquire_timeout_seconds
        self.navigation_timeout_ms = navigation_timeout_ms
        self.operation_timeout_seconds = operation_timeout_seconds
        self.preview_operation_timeout_seconds = preview_operation_timeout_seconds
        self.transfer_limit_bytes = transfer_limit_bytes
        self.preview_transfer_limit_bytes = preview_transfer_limit_bytes
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
            try:
                await browser.close()
            except Exception:
                pass
        if playwright is not None:
            try:
                await playwright.stop()
            except Exception:
                pass

    async def collect(self, url: str, selector: str) -> BrowserCollection:
        if not 1 <= len(selector) <= 512:
            raise JobValidationError("selector must contain 1-512 characters")

        async def extract(page, final_target) -> BrowserCollection:
            await page.wait_for_timeout(1500)
            locator = page.locator(selector)
            count = await locator.count()
            if count == 0:
                raise BrowserOperationError("SELECTOR_NOT_FOUND")
            if count != 1:
                raise BrowserOperationError("SELECTOR_NOT_UNIQUE")
            element = locator.first
            await element.evaluate(_ELEMENT_SETTLE_SCRIPT)
            await page.evaluate("window.stop()")
            await element.wait_for(state="attached", timeout=2_000)
            html = await element.evaluate(_CLEAN_HTML_SCRIPT)
            text = await element.inner_text()
            if len(html) > 1_000_000 or len(text) > 100_000:
                raise BrowserOperationError("EXTRACTED_CONTENT_TOO_LARGE")
            return BrowserCollection(
                final_target.url, html, text, (await page.title())[:120]
            )

        return await self._run(
            url,
            extract,
            blocked_resource_types=frozenset({"font", "image", "media"}),
        )

    async def preview(self, url: str) -> BrowserPreview:
        async def capture(page, final_target) -> BrowserPreview:
            await page.add_style_tag(
                content="*,*::before,*::after{animation:none!important;transition:none!important;scroll-behavior:auto!important;caret-color:transparent!important}"
            )
            await page.evaluate(_PAGE_SETTLE_SCRIPT)
            hit_map = await page.evaluate(_HIT_MAP_SCRIPT)
            await page.evaluate("window.stop()")
            width = min(1920, max(1, int(hit_map["width"])))
            height = min(3600, max(1, int(hit_map["height"])))
            try:
                screenshot = await page.screenshot(
                    type="jpeg",
                    quality=70,
                    clip={"x": 0, "y": 0, "width": width, "height": height},
                    scale="css",
                    animations="disabled",
                    caret="hide",
                    timeout=20_000,
                )
            except Exception as error:
                if error.__class__.__name__ == "TimeoutError":
                    raise BrowserOperationError("SCREENSHOT_TIMEOUT") from error
                raise
            if len(screenshot) > 2 * 1024 * 1024:
                raise BrowserOperationError("SCREENSHOT_TOO_LARGE")
            return BrowserPreview(
                final_url=final_target.url,
                page_title=(await page.title())[:120],
                screenshot_base64=base64.b64encode(screenshot).decode("ascii"),
                width=width,
                height=height,
                truncated_x=int(hit_map["width"]) > width,
                truncated_y=int(hit_map["height"]) > height,
                candidates=tuple(hit_map["candidates"]),
            )

        return await self._run(
            url,
            capture,
            transfer_limit_bytes=self.preview_transfer_limit_bytes,
            blocked_resource_types=frozenset({"media"}),
            operation_timeout_seconds=self.preview_operation_timeout_seconds,
        )

    async def _run(
        self,
        url: str,
        operation: PageOperation[T],
        *,
        transfer_limit_bytes: int | None = None,
        blocked_resource_types: frozenset[str] = frozenset(),
        operation_timeout_seconds: float | None = None,
    ) -> T:
        initial = self.url_policy.validate(url)
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(), timeout=self.acquire_timeout_seconds
            )
        except TimeoutError as error:
            raise BrowserOperationError("BROWSER_SATURATED") from error
        try:
            return await asyncio.wait_for(
                self._navigate(
                    initial.url,
                    operation,
                    transfer_limit_bytes=transfer_limit_bytes or self.transfer_limit_bytes,
                    blocked_resource_types=blocked_resource_types,
                ),
                timeout=operation_timeout_seconds or self.operation_timeout_seconds,
            )
        except TimeoutError as error:
            raise BrowserOperationError("BROWSER_TIMEOUT") from error
        finally:
            self._semaphore.release()

    async def _navigate(
        self,
        url: str,
        operation: PageOperation[T],
        *,
        transfer_limit_bytes: int,
        blocked_resource_types: frozenset[str],
    ) -> T:
        context = None
        page = None
        transferred = 0
        request_transfers: dict[str, int] = {}
        transfer_exceeded = asyncio.Event()
        overflow_task = None
        cdp = None

        async def route_request(route) -> None:
            if route.request.resource_type in {
                "websocket",
                "eventsource",
                *blocked_resource_types,
            }:
                await route.abort("blockedbyclient")
                return
            try:
                self.url_policy.validate(route.request.url)
            except JobValidationError:
                await route.abort("blockedbyclient")
                return
            await route.continue_()

        async def close_popup(popup) -> None:
            await popup.close()

        async def cancel_download(download) -> None:
            await download.cancel()

        def record_transfer(event, *, finished: bool = False) -> None:
            nonlocal transferred, overflow_task
            request_id = str(event.get("requestId") or "")
            size = int(event.get("encodedDataLength") or event.get("dataLength") or 0)
            if finished:
                size = max(0, size - request_transfers.get(request_id, 0))
            request_transfers[request_id] = request_transfers.get(request_id, 0) + size
            transferred += size
            if transferred > transfer_limit_bytes and not transfer_exceeded.is_set():
                transfer_exceeded.set()
                overflow_task = asyncio.create_task(page.close())

        try:
            await self.start()
            context = await self._browser.new_context(
                accept_downloads=False,
                service_workers="block",
                viewport={"width": 1280, "height": 900},
                device_scale_factor=1,
            )
            page = await context.new_page()
            await context.route("**/*", route_request)
            page.on("popup", close_popup)
            page.on("download", cancel_download)
            cdp = await context.new_cdp_session(page)
            cdp.on("Network.dataReceived", record_transfer)
            cdp.on(
                "Network.loadingFinished",
                lambda event: record_transfer(event, finished=True),
            )
            await cdp.send("Network.enable")
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.navigation_timeout_ms,
            )
            if transfer_exceeded.is_set():
                raise BrowserOperationError("TRANSFER_LIMIT_EXCEEDED")
            return await operation(page, self.url_policy.validate(page.url))
        except BrowserOperationError:
            raise
        except Exception as error:
            if transfer_exceeded.is_set():
                raise BrowserOperationError("TRANSFER_LIMIT_EXCEEDED") from error
            if (
                error.__class__.__name__ == "TargetClosedError"
                or self._browser is None
                or not self._browser.is_connected()
            ):
                await self.close()
                raise BrowserOperationError("BROWSER_CRASHED") from error
            raise BrowserOperationError("BROWSER_NAVIGATION_FAILED") from error
        finally:
            if overflow_task is not None:
                await asyncio.gather(overflow_task, return_exceptions=True)
            if cdp is not None:
                try:
                    await cdp.detach()
                except Exception:
                    pass
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
