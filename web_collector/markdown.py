from __future__ import annotations

import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from markdownify import markdownify

_REMOVE_TAGS = ("script", "style", "noscript", "template", "form", "svg", "canvas")
_BLANK_LINES = re.compile(r"\n{3,}")


def html_to_markdown(html: str, *, final_url: str) -> str:
    if len(html) > 1_000_000:
        raise ValueError("extracted HTML exceeds the allowed size")
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(_REMOVE_TAGS):
        element.decompose()
    for image in soup.find_all("img"):
        alt = " ".join(str(image.get("alt") or "이미지").split())[:160]
        image.replace_with(soup.new_string(f"[{alt}]"))
    for anchor in soup.find_all("a"):
        href = str(anchor.get("href") or "").strip()
        resolved = urljoin(final_url, href)
        parsed = urlsplit(resolved)
        if parsed.scheme.casefold() in {"http", "https"} and parsed.hostname:
            anchor["href"] = resolved
        else:
            anchor.attrs.pop("href", None)
    converted = markdownify(
        str(soup),
        heading_style="ATX",
        bullets="-",
        strip=("html", "head", "body"),
    )
    lines = [line.rstrip() for line in converted.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized = _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()
    return unicodedata.normalize("NFC", normalized)
