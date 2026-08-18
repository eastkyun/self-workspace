from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from job_platform import JobRepository, JobSchedule, JobTypeRegistry, ScheduleKind
from job_platform.database import connect, initialize_database
from job_platform.errors import JobValidationError
from job_platform.errors import JobConflict
from web_collector.adapter import WebCollectionAdapter
from web_collector.browser import BrowserCollection, BrowserOperationError
from web_collector.database import SCHEMA_SQL
from web_collector.database import initialize_database as initialize_web_collection_database
from web_collector.database import verify_web_collection_schema
from web_collector.repository import FormatterProfileRepository
from web_collector.financial_briefing import DEFAULT_FINANCIAL_FORMATTER_ID


def build_repository(tmp_path):
    database = initialize_database(tmp_path / "jobs.db")
    initialize_web_collection_database(database)
    adapter = WebCollectionAdapter()
    return database, adapter, JobRepository(database, JobTypeRegistry([adapter]))


def test_web_collection_schema_and_type_row_are_atomic_and_tenant_scoped(tmp_path):
    database, adapter, repository = build_repository(tmp_path)
    initialize_web_collection_database(database)
    verify_web_collection_schema(database)
    job = repository.create_job(
        workspace_id="workspace-a",
        actor_id="user-a",
        name="공지 수집",
        job_type="WEB_COLLECTION",
        schedule=JobSchedule(ScheduleKind.INTERVAL, "300"),
        type_config={
            "url": "https://example.com/notices",
            "selector": "#notice-list",
            "selector_metadata": {"text": "공지"},
            "note_policy": "CHANGED_ONLY",
            "extract_links": True,
            "extract_images": False,
        },
        now=datetime(2026, 8, 13, tzinfo=UTC),
    )
    with connect(database) as connection:
        projected = adapter.project_job_detail(connection, job)
        assert projected["url"] == "https://example.com/notices"
        assert projected["selector_metadata"] == {"text": "공지"}
        assert projected["output_format"] == "RAW_MARKDOWN"
        assert connection.execute(
            "SELECT COUNT(*) FROM web_collection_jobs WHERE workspace_id='workspace-a'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM web_collection_jobs WHERE workspace_id='workspace-b'"
        ).fetchone()[0] == 0


def test_web_collection_initializer_adds_output_format_to_existing_schema(tmp_path):
    database = initialize_database(tmp_path / "jobs.db")
    legacy_schema = SCHEMA_SQL.replace(
        "    output_format TEXT NOT NULL DEFAULT 'RAW_MARKDOWN'\n"
        "        CHECK(output_format IN ('RAW_MARKDOWN','FINANCIAL_MARKET_BRIEFING')),\n",
        "",
    )
    with connect(database) as connection:
        connection.executescript(legacy_schema)

    initialize_web_collection_database(database)

    with connect(database) as connection:
        columns = {
            row[1]: row for row in connection.execute(
                "PRAGMA table_info(web_collection_jobs)"
            )
        }
    assert columns["output_format"][4] == "'RAW_MARKDOWN'"
    assert "formatter_profile_id" in columns


def test_formatter_profile_update_pauses_and_invalidates_dependent_job(tmp_path):
    database, _, repository = build_repository(tmp_path)
    profiles = FormatterProfileRepository(database)
    profile = profiles.create(
        "workspace-a",
        "user-a",
        name="시장 요약",
        instructions="원문의 값만 사용해 Markdown으로 정리한다.",
        model="gpt-5.6-luna",
        now=datetime(2026, 8, 13, tzinfo=UTC),
    )
    job = repository.create_job(
        workspace_id="workspace-a",
        actor_id="user-a",
        name="시장 수집",
        job_type="WEB_COLLECTION",
        schedule=JobSchedule(ScheduleKind.MANUAL),
        type_config={
            "url": "https://example.com",
            "selector": "main",
            "output_format": "FINANCIAL_MARKET_BRIEFING",
            "formatter_profile_id": profile["id"],
        },
        now=datetime(2026, 8, 13, tzinfo=UTC),
    )
    repository.mark_tested("workspace-a", job["id"], 1)
    repository.activate("workspace-a", job["id"], "user-a")

    changed = profiles.update(
        "workspace-a",
        profile["id"],
        "user-a",
        name="시장 요약",
        instructions="표 형식으로만 정리한다.",
        model="gpt-5.6-luna",
        expected_revision=1,
    )

    refreshed = repository.get_job("workspace-a", job["id"])
    assert changed["revision"] == 2
    assert refreshed["status"] == "PAUSED"
    assert refreshed["definition_revision"] == 2
    assert refreshed["last_tested_revision"] is None
    with pytest.raises(JobConflict, match="used by a Job"):
        profiles.delete("workspace-a", profile["id"])


def test_builtin_financial_profile_can_be_updated_and_restored(tmp_path):
    database, _, repository = build_repository(tmp_path)
    profiles = FormatterProfileRepository(database)
    builtin = profiles.get("workspace-a", DEFAULT_FINANCIAL_FORMATTER_ID)
    assert builtin["is_builtin"] is True
    assert builtin["is_overridden"] is False
    assert "Financial Market Briefing Formatter" in builtin["instructions"]

    job = repository.create_job(
        workspace_id="workspace-a",
        actor_id="user-a",
        name="기본 시장 수집",
        job_type="WEB_COLLECTION",
        schedule=JobSchedule(ScheduleKind.MANUAL),
        type_config={
            "url": "https://example.com",
            "selector": "main",
            "output_format": "FINANCIAL_MARKET_BRIEFING",
        },
    )
    repository.mark_tested("workspace-a", job["id"], 1)
    repository.activate("workspace-a", job["id"], "user-a")

    changed = profiles.update(
        "workspace-a",
        DEFAULT_FINANCIAL_FORMATTER_ID,
        "user-a",
        name="내 금융시장 브리핑",
        instructions="입력 수치만 사용해 # 📈 주요 금융시장 동향으로 시작한다.",
        model="gpt-5.6-luna",
        expected_revision=1,
    )
    paused = repository.get_job("workspace-a", job["id"])
    assert changed["is_overridden"] is True
    assert changed["revision"] == 2
    assert paused["status"] == "PAUSED"
    assert paused["definition_revision"] == 2
    assert paused["last_tested_revision"] is None

    restored = profiles.delete(
        "workspace-a", DEFAULT_FINANCIAL_FORMATTER_ID, "user-a"
    )
    default_again = profiles.get("workspace-a", DEFAULT_FINANCIAL_FORMATTER_ID)
    refreshed = repository.get_job("workspace-a", job["id"])
    assert restored["reset"] is True
    assert default_again["is_overridden"] is False
    assert default_again["revision"] == 1
    assert refreshed["definition_revision"] == 3


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"url": "https://example.com", "selector": ""},
        {"url": "https://example.com", "selector": "#x", "extract_images": True},
        {"url": "https://example.com", "selector": "#x", "unknown": 1},
        {"url": "https://example.com", "selector": "#x", "fallback_x": -1},
    ],
)
def test_web_collection_config_fails_closed(payload):
    with pytest.raises(JobValidationError):
        WebCollectionAdapter().validate_config(payload)


class FakeBrowser:
    async def collect(self, url, selector):
        return BrowserCollection(
            final_url=url,
            content_html="<article><h1>공지</h1><p>본문</p></article>",
            content_text="공지\n본문",
            page_title="페이지 제목",
        )

    async def close(self):
        return None


class FailingBrowser(FakeBrowser):
    async def collect(self, url, selector):
        raise BrowserOperationError("SELECTOR_NOT_UNIQUE")


def test_adapter_test_converts_bounded_markdown_preview():
    adapter = WebCollectionAdapter(execution_enabled=True, browser_manager=FakeBrowser())
    config = adapter.validate_config({"url": "https://example.com/", "selector": "main"})

    outcome = __import__("asyncio").run(adapter.test(object(), config))

    assert outcome.success is True
    assert outcome.code == "SELECTOR_VERIFIED"
    assert outcome.diagnostics["markdown_preview"] == "# 공지\n\n본문"
    assert len(outcome.diagnostics["content_hash"]) == 64


def test_adapter_test_returns_safe_browser_error_code():
    adapter = WebCollectionAdapter(execution_enabled=True, browser_manager=FailingBrowser())
    config = adapter.validate_config({"url": "https://example.com/", "selector": "main"})

    outcome = __import__("asyncio").run(adapter.test(object(), config))

    assert outcome.success is False
    assert outcome.code == "SELECTOR_NOT_UNIQUE"
    assert outcome.diagnostics == {}


class FakeFinancialFormatter:
    def __init__(self):
        self.calls = []

    async def format(self, **payload):
        self.calls.append(payload)
        return "# 📈 주요 금융시장 동향\n\n> 정리됨"


def test_adapter_test_applies_opt_in_financial_briefing_formatter():
    formatter = FakeFinancialFormatter()
    adapter = WebCollectionAdapter(
        execution_enabled=True,
        browser_manager=FakeBrowser(),
        financial_briefing_formatter=formatter,
        clock=lambda: datetime(2026, 8, 13, 4, 0, tzinfo=UTC),
    )
    config = adapter.validate_config(
        {
            "url": "https://example.com/",
            "selector": "main",
            "output_format": "FINANCIAL_MARKET_BRIEFING",
        }
    )

    outcome = __import__("asyncio").run(adapter.test(object(), config))

    assert outcome.success is True
    assert outcome.diagnostics["markdown_preview"].startswith(
        "# 📈 주요 금융시장 동향"
    )
    assert formatter.calls == [
        {
            "source": "https://example.com/",
            "collected_at": "2026-08-13 13:00:00 KST",
            "raw_content": "# 공지\n\n본문",
            "instructions": None,
            "model": None,
            "required_prefix": "# 📈 주요 금융시장 동향",
        }
    ]


def test_adapter_test_fails_closed_when_financial_formatter_is_unavailable():
    adapter = WebCollectionAdapter(
        execution_enabled=True,
        browser_manager=FakeBrowser(),
    )
    config = adapter.validate_config(
        {
            "url": "https://example.com/",
            "selector": "main",
            "output_format": "FINANCIAL_MARKET_BRIEFING",
        }
    )

    outcome = __import__("asyncio").run(adapter.test(object(), config))

    assert outcome.success is False
    assert outcome.code == "AI_FORMATTER_UNAVAILABLE"


def test_adapter_test_uses_workspace_formatter_profile(tmp_path):
    database = initialize_database(tmp_path / "jobs.db")
    initialize_web_collection_database(database)
    profile = FormatterProfileRepository(database).create(
        "workspace-a",
        "user-a",
        name="사용자 요약",
        instructions="제목과 세 개의 bullet로 정리한다.",
        model="gpt-5.6-luna",
    )
    formatter = FakeFinancialFormatter()
    adapter = WebCollectionAdapter(
        execution_enabled=True,
        browser_manager=FakeBrowser(),
        database_path=database,
        financial_briefing_formatter=formatter,
    )
    config = adapter.validate_config(
        {
            "url": "https://example.com/",
            "selector": "main",
            "output_format": "FINANCIAL_MARKET_BRIEFING",
            "formatter_profile_id": profile["id"],
        }
    )

    outcome = __import__("asyncio").run(
        adapter.test(SimpleNamespace(workspace_id="workspace-a"), config)
    )

    assert outcome.success is True
    assert formatter.calls[0]["instructions"] == "제목과 세 개의 bullet로 정리한다."
    assert formatter.calls[0]["model"] == "gpt-5.6-luna"
    assert formatter.calls[0]["required_prefix"] is None


def test_adapter_test_uses_managed_builtin_financial_profile(tmp_path):
    database = initialize_database(tmp_path / "jobs.db")
    initialize_web_collection_database(database)
    FormatterProfileRepository(database).update(
        "workspace-a",
        DEFAULT_FINANCIAL_FORMATTER_ID,
        "user-a",
        name="내 금융 브리핑",
        instructions="관리 화면에서 바꾼 금융시장 정리 지침",
        model="gpt-5.6-luna",
        expected_revision=1,
    )
    formatter = FakeFinancialFormatter()
    adapter = WebCollectionAdapter(
        execution_enabled=True,
        browser_manager=FakeBrowser(),
        database_path=database,
        financial_briefing_formatter=formatter,
    )
    config = adapter.validate_config(
        {
            "url": "https://example.com/",
            "selector": "main",
            "output_format": "FINANCIAL_MARKET_BRIEFING",
        }
    )

    outcome = __import__("asyncio").run(
        adapter.test(SimpleNamespace(workspace_id="workspace-a"), config)
    )

    assert outcome.success is True
    assert formatter.calls[0]["instructions"] == "관리 화면에서 바꾼 금융시장 정리 지침"
    assert formatter.calls[0]["required_prefix"] is None
