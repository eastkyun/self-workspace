from __future__ import annotations

import json
import secrets
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
PORTAL_DIR = Path(__file__).resolve().parent
DEFAULT_CORPUS_PATH = PORTAL_DIR / "quotes.json"
MAX_ID_LENGTH = 64
MAX_TEXT_LENGTH = 180
MAX_AUTHOR_LENGTH = 80
MAX_SOURCE_LENGTH = 120


@dataclass(frozen=True, slots=True)
class Quote:
    id: str
    text: str
    author: str
    source: str | None = None
    enabled: bool = True


type QuoteSelector = Callable[[Sequence[Quote]], Quote]


class QuoteCorpusError(ValueError):
    def __init__(self, errors: Sequence[str]) -> None:
        self.errors = tuple(errors)
        super().__init__("Invalid quote corpus:\n- " + "\n- ".join(self.errors))


def _clean_required_string(
    value: Any,
    *,
    field: str,
    index: int,
    maximum: int,
    errors: list[str],
) -> str:
    if not isinstance(value, str):
        errors.append(f"quotes[{index}].{field} must be a string")
        return ""
    cleaned = " ".join(value.split())
    if not cleaned:
        errors.append(f"quotes[{index}].{field} must not be blank")
    elif len(cleaned) > maximum:
        errors.append(
            f"quotes[{index}].{field} must be at most {maximum} characters"
        )
    return cleaned


def _clean_source(
    value: Any,
    *,
    index: int,
    errors: list[str],
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        errors.append(f"quotes[{index}].source must be a string or null")
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > MAX_SOURCE_LENGTH:
        errors.append(
            f"quotes[{index}].source must be at most "
            f"{MAX_SOURCE_LENGTH} characters"
        )
    return cleaned


def parse_quote_corpus(document: Any) -> tuple[Quote, ...]:
    errors: list[str] = []
    if not isinstance(document, Mapping):
        raise QuoteCorpusError(("document must be an object",))

    schema_version = document.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {SCHEMA_VERSION}, got {schema_version!r}"
        )

    raw_quotes = document.get("quotes")
    if not isinstance(raw_quotes, list):
        errors.append("quotes must be an array")
        raw_quotes = []

    quotes: list[Quote] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw_quotes):
        if not isinstance(item, Mapping):
            errors.append(f"quotes[{index}] must be an object")
            continue

        quote_id = _clean_required_string(
            item.get("id"),
            field="id",
            index=index,
            maximum=MAX_ID_LENGTH,
            errors=errors,
        )
        text = _clean_required_string(
            item.get("text"),
            field="text",
            index=index,
            maximum=MAX_TEXT_LENGTH,
            errors=errors,
        )
        author = _clean_required_string(
            item.get("author"),
            field="author",
            index=index,
            maximum=MAX_AUTHOR_LENGTH,
            errors=errors,
        )
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            errors.append(f"quotes[{index}].enabled must be a boolean")
            enabled = False
        source = _clean_source(item.get("source"), index=index, errors=errors)

        if quote_id:
            if quote_id in seen_ids:
                errors.append(f"duplicate quote id: {quote_id}")
            seen_ids.add(quote_id)

        quotes.append(
            Quote(
                id=quote_id,
                text=text,
                author=author,
                source=source,
                enabled=enabled,
            )
        )

    if errors:
        raise QuoteCorpusError(errors)
    return tuple(quotes)


def load_quote_corpus(path: str | Path) -> tuple[Quote, ...]:
    corpus_path = Path(path)
    try:
        document = json.loads(corpus_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise QuoteCorpusError(
            (
                f"{corpus_path} contains invalid JSON at "
                f"line {error.lineno}, column {error.colno}",
            )
        ) from error
    return parse_quote_corpus(document)


def load_default_quote_corpus() -> tuple[Quote, ...]:
    try:
        return load_quote_corpus(DEFAULT_CORPUS_PATH)
    except OSError:
        return ()


def get_enabled_quotes(quotes: Sequence[Quote]) -> tuple[Quote, ...]:
    return tuple(quote for quote in quotes if quote.enabled)


def choose_quote(
    quotes: Sequence[Quote],
    *,
    selector: QuoteSelector | None = None,
) -> Quote | None:
    enabled_quotes = get_enabled_quotes(quotes)
    if not enabled_quotes:
        return None
    return (selector or secrets.choice)(enabled_quotes)


DEFAULT_QUOTES = load_default_quote_corpus()
