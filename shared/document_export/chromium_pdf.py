from __future__ import annotations

import sys
from pathlib import Path


HEADING_PAGE_BREAK_THRESHOLD = 0.6
PRINTABLE_A4_HEIGHT_MM = 297 - (12 * 2)


def apply_heading_page_breaks(page) -> int:
    """Move body headings out of the bottom 40% of an A4 content area."""
    return page.evaluate(
        """
        ({ printableHeightMm, threshold }) => {
          const millimetre = document.createElement("div");
          millimetre.style.cssText = [
            "position:absolute",
            "visibility:hidden",
            "pointer-events:none",
            "height:100mm",
          ].join(";");
          document.body.append(millimetre);
          const printableHeight = millimetre.getBoundingClientRect().height
            * printableHeightMm / 100;
          millimetre.remove();

          let insertedSpace = 0;
          let breakCount = 0;
          const headings = document.querySelectorAll(
            ".markdown-viewer__content h1, .markdown-viewer__content h2"
          );

          for (const heading of headings) {
            const adjustedTop = heading.getBoundingClientRect().top
              + window.scrollY + insertedSpace;
            const positionOnPage = adjustedTop % printableHeight;
            if (positionOnPage >= printableHeight * threshold) {
              heading.classList.add("pdf-page-break-before");
              insertedSpace += printableHeight - positionOnPage;
              breakCount += 1;
            }
          }
          return breakCount;
        }
        """,
        {
            "printableHeightMm": PRINTABLE_A4_HEIGHT_MM,
            "threshold": HEADING_PAGE_BREAK_THRESHOLD,
        },
    )


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
            apply_heading_page_breaks(page)
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
