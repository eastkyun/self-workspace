from __future__ import annotations

import sys
from pathlib import Path


def render_pdf(
    chromium_path: str,
    html_path: Path,
    output_path: Path,
    timeout_ms: int,
) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chromium_path,
            headless=True,
            args=(
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-background-networking",
                "--disable-component-update",
            ),
        )
        try:
            context = browser.new_context()

            def route_request(route) -> None:
                if route.request.url.startswith(("http://", "https://")):
                    route.abort()
                else:
                    route.continue_()

            context.route("**/*", route_request)
            page = context.new_page()
            page.set_default_timeout(timeout_ms)
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.emulate_media(media="print")
            page.pdf(
                path=str(output_path),
                format="A4",
                print_background=True,
                prefer_css_page_size=True,
                display_header_footer=False,
            )
        finally:
            browser.close()


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        return 2
    _, chromium_path, html_path, output_path, timeout = argv
    render_pdf(
        chromium_path,
        Path(html_path),
        Path(output_path),
        int(timeout),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
