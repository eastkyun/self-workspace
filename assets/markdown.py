from __future__ import annotations

import re

_IMAGE_DESTINATION = re.compile(
    r"!\[(?:\\.|[^\]\\])*\]\(\s*image://([1-9][0-9]*)\s*(?:\"[^\"\r\n]*\"|'[^'\r\n]*')?\s*\)"
)
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+)(.*?)(?<!`)\1(?!`)")


def extract_asset_ids(markdown: str) -> tuple[int, ...]:
    """Return unique image asset IDs in source order, excluding code spans/blocks."""
    visible: list[str] = []
    fence_marker: str | None = None
    for line in markdown.splitlines(keepends=True):
        marker = _FENCE.match(line)
        if fence_marker is not None:
            if marker and marker.group(1)[0] == fence_marker[0] and len(marker.group(1)) >= len(fence_marker):
                fence_marker = None
            continue
        if marker:
            fence_marker = marker.group(1)
            continue
        visible.append(_INLINE_CODE.sub("", line))

    found: list[int] = []
    seen: set[int] = set()
    for match in _IMAGE_DESTINATION.finditer("".join(visible)):
        asset_id = int(match.group(1))
        if asset_id not in seen:
            seen.add(asset_id)
            found.append(asset_id)
    return tuple(found)
