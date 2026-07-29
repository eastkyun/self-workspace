import json
from pathlib import Path

import pytest

from portal.quotes import (
    Quote,
    QuoteCorpusError,
    choose_quote,
    get_enabled_quotes,
    load_quote_corpus,
    parse_quote_corpus,
)


def document(*quotes: dict[str, object]) -> dict[str, object]:
    return {"schema_version": 1, "quotes": list(quotes)}


def quote(
    quote_id: str,
    *,
    text: str = "한 걸음",
    author: str = "저자",
    enabled: bool = True,
) -> dict[str, object]:
    return {
        "id": quote_id,
        "text": text,
        "author": author,
        "enabled": enabled,
    }


def test_loads_normalized_immutable_quotes(tmp_path: Path) -> None:
    path = tmp_path / "quotes.json"
    path.write_text(
        json.dumps(
            document(
                {
                    **quote("one", text="  한   걸음  "),
                    "source": "  출처   문서 ",
                }
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    quotes = load_quote_corpus(path)

    assert quotes == (
        Quote(
            id="one",
            text="한 걸음",
            author="저자",
            source="출처 문서",
            enabled=True,
        ),
    )
    assert isinstance(quotes, tuple)


def test_filters_disabled_quotes_and_selects_deterministically() -> None:
    quotes = parse_quote_corpus(
        document(
            quote("disabled", enabled=False),
            quote("first"),
            quote("last"),
        )
    )

    assert [item.id for item in get_enabled_quotes(quotes)] == ["first", "last"]
    assert choose_quote(quotes, selector=lambda items: items[-1]).id == "last"


def test_empty_or_disabled_corpus_uses_no_quote() -> None:
    assert choose_quote(()) is None
    assert choose_quote((Quote("off", "문장", "저자", enabled=False),)) is None


def test_validation_collects_all_structural_errors() -> None:
    with pytest.raises(QuoteCorpusError) as captured:
        parse_quote_corpus(
            {
                "schema_version": 9,
                "quotes": [
                    quote("same", text=" "),
                    quote("same", author=" "),
                    {**quote("bad-enabled"), "enabled": "yes"},
                    "not-an-object",
                ],
            }
        )

    errors = captured.value.errors
    assert any("schema_version" in error for error in errors)
    assert any("text must not be blank" in error for error in errors)
    assert any("author must not be blank" in error for error in errors)
    assert any("duplicate quote id" in error for error in errors)
    assert any("enabled must be a boolean" in error for error in errors)
    assert any("must be an object" in error for error in errors)


@pytest.mark.parametrize(
    "payload, message",
    [
        ([], "document must be an object"),
        ({"schema_version": 1, "quotes": {}}, "quotes must be an array"),
    ],
)
def test_rejects_invalid_document_shapes(
    payload: object,
    message: str,
) -> None:
    with pytest.raises(QuoteCorpusError, match=message):
        parse_quote_corpus(payload)


def test_reports_invalid_json_location(tmp_path: Path) -> None:
    path = tmp_path / "quotes.json"
    path.write_text('{"schema_version": 1,', encoding="utf-8")

    with pytest.raises(QuoteCorpusError) as captured:
        load_quote_corpus(path)

    assert "line 1" in str(captured.value)
    assert "column" in str(captured.value)
