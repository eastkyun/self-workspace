from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from web_collector.financial_briefing import (
    FinancialBriefingError,
    OpenAIFinancialBriefingFormatter,
)


class FakeResponses:
    def __init__(self, output_text: str | None = None, error: Exception | None = None):
        self.output_text = output_text
        self.error = error
        self.calls = []

    async def create(self, **payload):
        self.calls.append(payload)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(output_text=self.output_text)


class FakeClient:
    def __init__(self, responses: FakeResponses):
        self.responses = responses


def test_formatter_uses_responses_api_without_storing_raw_market_data():
    responses = FakeResponses(
        "# 📈 주요 금융시장 동향\n\n## 🇰🇷 국내 시장\n\n| KOSPI | 1 | 🟢 +1% | — |"
    )
    formatter = OpenAIFinancialBriefingFormatter(
        api_key="test-key",
        client=FakeClient(responses),
    )

    markdown = asyncio.run(
        formatter.format(
            source="https://example.com/markets",
            collected_at="2026-08-14 14:30:00 KST",
            raw_content="KOSPI 1 +1%",
        )
    )

    assert markdown.startswith("# 📈 주요 금융시장 동향")
    request = responses.calls[0]
    assert request["model"] == "gpt-5.6-luna"
    assert request["store"] is False
    assert "Financial Market Briefing Formatter" in request["instructions"]
    assert "# INPUT" not in request["instructions"]
    assert "https://example.com/markets" in request["input"]
    assert "KOSPI 1 +1%" in request["input"]
    assert "test-key" not in str(request)


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (SimpleNamespace(status_code=401), "AI_FORMATTER_AUTH_FAILED"),
        (SimpleNamespace(status_code=429), "AI_FORMATTER_RATE_LIMITED"),
    ],
)
def test_formatter_maps_safe_api_error_codes(error, code):
    exception = RuntimeError("secret upstream detail")
    exception.status_code = error.status_code
    formatter = OpenAIFinancialBriefingFormatter(
        api_key="test-key",
        client=FakeClient(FakeResponses(error=exception)),
    )

    with pytest.raises(FinancialBriefingError, match=code):
        asyncio.run(
            formatter.format(
                source="https://example.com",
                collected_at="2026-08-14 14:30:00 KST",
                raw_content="raw",
            )
        )


def test_formatter_rejects_non_markdown_or_fenced_output():
    formatter = OpenAIFinancialBriefingFormatter(
        api_key="test-key",
        client=FakeClient(FakeResponses("```markdown\n# 잘못된 응답\n```")),
    )

    with pytest.raises(FinancialBriefingError, match="AI_FORMATTER_INVALID_OUTPUT"):
        asyncio.run(
            formatter.format(
                source="https://example.com",
                collected_at="2026-08-14 14:30:00 KST",
                raw_content="raw",
            )
        )


def test_formatter_accepts_custom_instructions_model_and_generic_markdown():
    responses = FakeResponses("# 사용자 정의 요약\n\n- 원문 기반")
    formatter = OpenAIFinancialBriefingFormatter(
        api_key="test-key",
        client=FakeClient(responses),
    )

    markdown = asyncio.run(
        formatter.format(
            source="https://example.com",
            collected_at="2026-08-14 14:30:00 KST",
            raw_content="raw",
            instructions="사용자가 정의한 Markdown 정리 지침",
            model="gpt-5.6-luna",
            required_prefix=None,
        )
    )

    assert markdown.startswith("# 사용자 정의 요약")
    assert responses.calls[0]["instructions"] == "사용자가 정의한 Markdown 정리 지침"
    assert responses.calls[0]["model"] == "gpt-5.6-luna"
