from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_platform import JobRepository, JobSchedule, JobTypeRegistry, ScheduleKind
from job_platform.database import connect, initialize_database
from job_platform.errors import JobValidationError
from web_collector.adapter import WebCollectionAdapter
from web_collector.browser import BrowserCollection, BrowserOperationError
from web_collector.database import initialize_database as initialize_web_collection_database
from web_collector.database import verify_web_collection_schema


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
        assert connection.execute(
            "SELECT COUNT(*) FROM web_collection_jobs WHERE workspace_id='workspace-a'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM web_collection_jobs WHERE workspace_id='workspace-b'"
        ).fetchone()[0] == 0


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
