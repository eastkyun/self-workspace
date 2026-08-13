from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum

from job_platform.errors import JobValidationError


class NotePolicy(StrEnum):
    CHANGED_ONLY = "CHANGED_ONLY"
    EVERY_RUN = "EVERY_RUN"


@dataclass(frozen=True, slots=True)
class WebCollectionConfig:
    url: str
    selector: str
    selector_metadata_json: str = "{}"
    fallback_x: float | None = None
    fallback_y: float | None = None
    fallback_width: float | None = None
    fallback_height: float | None = None
    note_policy: NotePolicy = NotePolicy.CHANGED_ONLY
    extract_links: bool = True
    extract_images: bool = False

    @classmethod
    def parse(cls, payload: object) -> "WebCollectionConfig":
        if not isinstance(payload, dict):
            raise JobValidationError("Web Collection configuration must be an object")
        unknown = set(payload) - {
            "url", "selector", "selector_metadata", "fallback_x", "fallback_y",
            "fallback_width", "fallback_height", "note_policy", "extract_links", "extract_images",
        }
        if unknown:
            raise JobValidationError("Web Collection configuration contains unknown fields")
        url = str(payload.get("url") or "").strip()
        selector = str(payload.get("selector") or "").strip()
        if not 1 <= len(url) <= 2048:
            raise JobValidationError("URL must contain 1-2048 characters")
        if not 1 <= len(selector) <= 512:
            raise JobValidationError("selector must contain 1-512 characters")
        metadata = payload.get("selector_metadata", {})
        if not isinstance(metadata, dict):
            raise JobValidationError("selector metadata must be an object")
        metadata_json = json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        if len(metadata_json) > 16384:
            raise JobValidationError("selector metadata exceeds the allowed size")
        try:
            note_policy = NotePolicy(str(payload.get("note_policy") or NotePolicy.CHANGED_ONLY))
        except ValueError as error:
            raise JobValidationError("unsupported Note policy") from error
        extract_links = payload.get("extract_links", True)
        extract_images = payload.get("extract_images", False)
        if not isinstance(extract_links, bool) or extract_images is not False:
            raise JobValidationError("image extraction is unavailable and link policy must be boolean")
        geometry = []
        for key in ("fallback_x", "fallback_y", "fallback_width", "fallback_height"):
            value = payload.get(key)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0):
                raise JobValidationError("fallback geometry must contain non-negative numbers")
            geometry.append(float(value) if value is not None else None)
        return cls(
            url=url, selector=selector, selector_metadata_json=metadata_json,
            fallback_x=geometry[0], fallback_y=geometry[1], fallback_width=geometry[2],
            fallback_height=geometry[3], note_policy=note_policy,
            extract_links=extract_links, extract_images=False,
        )
