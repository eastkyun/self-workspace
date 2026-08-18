from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from access.context import WorkspaceContext
from apps.notes.app.database import initialize_database as initialize_notes_database
from apps.notes.app.service import NotesService
from job_platform import JobRepository, JobSchedule, JobTypeRegistry, ScheduleKind
from job_platform.application import JobApplicationService
from job_platform.database import connect, initialize_database
from job_platform.service import JobWorker
from job_platform.schemas import JobExecutionContext, RunStatus, TriggerKind
from web_collector.adapter import WebCollectionAdapter
from web_collector.browser import BrowserCollection
from web_collector.database import initialize_database as initialize_web_schema


NOW = datetime(2026, 8, 13, 4, 0, tzinfo=UTC)


class FakeBrowser:
    def __init__(self, html="<article><h1>공지</h1><p>본문</p></article>"):
        self.html = html

    async def collect(self, url, selector):
        return BrowserCollection(url, self.html, "공지\n본문", "페이지 제목")

    async def close(self):
        return None


class PolicyRejectedBrowser(FakeBrowser):
    async def collect(self, url, selector):
        from job_platform.errors import JobValidationError

        raise JobValidationError("target host could not be resolved")


class FakeNotes:
    def __init__(self, *, fail_once=False):
        self.fail_once = fail_once
        self.calls = []
        self.keys = {}

    def create_note(self, context, payload, *, idempotency_key=None):
        self.calls.append((context, payload, idempotency_key))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("simulated Notes outage")
        return self.keys.setdefault(idempotency_key, {"id": len(self.keys) + 1})


class FakeFinancialFormatter:
    def __init__(self):
        self.calls = []

    async def format(self, **payload):
        self.calls.append(payload)
        return (
            "# 📈 주요 금융시장 동향\n\n"
            f"> **수집 출처:** {payload['source']}\n"
            f"> **수집 시각:** {payload['collected_at']}\n\n"
            "## 🇰🇷 국내 시장\n\n| 지표 | 현재가 | 등락 | 주요 이슈 |"
        )


class AllowAuthorization:
    def require(self, *_args, **_kwargs):
        return object()

    def authorize(self, *_args, **_kwargs):
        return True


def build_execution(
    tmp_path,
    *,
    policy="CHANGED_ONLY",
    notes=None,
    output_format="RAW_MARKDOWN",
    formatter=None,
):
    database = initialize_database(tmp_path / "jobs.db")
    initialize_web_schema(database)
    notes = notes or FakeNotes()
    adapter = WebCollectionAdapter(
        execution_enabled=True,
        browser_manager=FakeBrowser(),
        database_path=database,
        notes_service=notes,
        context_resolver=lambda workspace_id, actor_id: {
            "workspace_id": workspace_id, "actor_id": actor_id
        },
        financial_briefing_formatter=formatter,
        clock=lambda: NOW,
    )
    repository = JobRepository(database, JobTypeRegistry([adapter]))
    job = repository.create_job(
        workspace_id="workspace-a",
        actor_id="user-a",
        name="공지 수집",
        job_type="WEB_COLLECTION",
        schedule=JobSchedule(ScheduleKind.MANUAL),
        type_config={
            "url": "https://example.com/notices",
            "selector": "article",
            "note_policy": policy,
            "output_format": output_format,
        },
        now=NOW,
    )
    assert repository.mark_tested("workspace-a", job["id"], job["definition_revision"], now=NOW)
    job = repository.activate("workspace-a", job["id"], "user-a", now=NOW)
    return database, repository, adapter, job, notes


def context_for(repository, job, *, now=NOW):
    run = repository.enqueue_manual("workspace-a", job["id"], now=now)
    # The adapter is exercised directly here; mark the synthetic core run
    # terminal so the next scenario run does not violate one-active-run fencing.
    with connect(repository.database_path) as connection:
        connection.execute(
            "UPDATE job_runs SET status='SUCCEEDED',finished_at=?,outcome_code='TEST' WHERE id=?",
            (now.isoformat().replace("+00:00", "Z"), run["id"]),
        )
    return JobExecutionContext(
        run_id=run["id"],
        job_id=job["id"],
        workspace_id="workspace-a",
        actor_id="user-a",
        trigger_kind=TriggerKind.MANUAL,
        scheduled_for=now,
    )


def test_changed_content_creates_one_note_and_unchanged_content_does_not(tmp_path):
    database, repository, adapter, job, notes = build_execution(tmp_path)

    first = asyncio.run(adapter.execute(context_for(repository, job)))
    second = asyncio.run(adapter.execute(context_for(repository, job)))

    assert first.status is RunStatus.SUCCEEDED
    assert first.outcome_code == "NOTE_CREATED"
    assert second.status is RunStatus.UNCHANGED
    assert len(notes.calls) == 1
    with __import__("sqlite3").connect(database) as connection:
        rows = connection.execute(
            "SELECT changed,note_status,note_id FROM web_collection_results ORDER BY id"
        ).fetchall()
    assert sorted(rows) == [(0, "NOT_REQUIRED", None), (1, "CREATED", 1)]


def test_every_run_policy_creates_notes_for_unchanged_content(tmp_path):
    _, repository, adapter, job, notes = build_execution(
        tmp_path, policy="EVERY_RUN"
    )

    assert asyncio.run(adapter.execute(context_for(repository, job))).status is RunStatus.SUCCEEDED
    assert asyncio.run(adapter.execute(context_for(repository, job))).status is RunStatus.SUCCEEDED

    assert len(notes.calls) == 2
    assert notes.calls[0][2] != notes.calls[1][2]


def test_financial_briefing_formats_only_changed_content_and_avoids_duplicate_provenance(
    tmp_path,
):
    formatter = FakeFinancialFormatter()
    _, repository, adapter, job, notes = build_execution(
        tmp_path,
        output_format="FINANCIAL_MARKET_BRIEFING",
        formatter=formatter,
    )

    first = asyncio.run(adapter.execute(context_for(repository, job)))
    second = asyncio.run(adapter.execute(context_for(repository, job)))

    assert first.outcome_code == "NOTE_CREATED"
    assert second.outcome_code == "CONTENT_UNCHANGED"
    assert len(formatter.calls) == 1
    payload = notes.calls[0][1]
    assert payload.content.startswith("# 📈 주요 금융시장 동향")
    assert payload.content.count("수집 출처") == 1
    assert payload.title == "공지 수집 - 2026-08-13 13:00:00"


def test_financial_briefing_execution_fails_closed_without_formatter(tmp_path):
    database, repository, adapter, job, notes = build_execution(
        tmp_path,
        output_format="FINANCIAL_MARKET_BRIEFING",
    )

    outcome = asyncio.run(adapter.execute(context_for(repository, job)))

    assert outcome.status is RunStatus.FAILED
    assert outcome.outcome_code == "AI_FORMATTER_UNAVAILABLE"
    assert notes.calls == []
    with connect(database) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM web_collection_results"
        ).fetchone()[0] == 0


def test_execution_reports_target_policy_failure_without_handler_error(tmp_path):
    _, repository, adapter, job, _ = build_execution(tmp_path)
    adapter.browser_manager = PolicyRejectedBrowser()

    outcome = asyncio.run(adapter.execute(context_for(repository, job)))

    assert outcome.status is RunStatus.FAILED
    assert outcome.outcome_code == "TARGET_POLICY_REJECTED"


def test_retry_reconciles_failed_note_with_source_run_idempotency_key(tmp_path):
    notes = FakeNotes(fail_once=True)
    _, repository, adapter, job, _ = build_execution(tmp_path, notes=notes)
    source_context = context_for(repository, job)

    failed = asyncio.run(adapter.execute(source_context))
    retry_context = context_for(repository, job)
    retried = asyncio.run(adapter.retry(retry_context, source_context.run_id))

    assert failed.status is RunStatus.PARTIAL
    assert failed.outcome_code == "NOTE_CREATE_FAILED"
    assert retried.status is RunStatus.SUCCEEDED
    assert notes.calls[0][2] == notes.calls[1][2]
    assert notes.calls[1][2].endswith(source_context.run_id)


def test_retry_recollects_when_the_failed_run_has_no_saved_result(tmp_path):
    _, repository, adapter, job, notes = build_execution(tmp_path)
    retry_context = context_for(repository, job)

    retried = asyncio.run(adapter.retry(retry_context, "missing-source-run"))

    assert retried.status is RunStatus.SUCCEEDED
    assert retried.outcome_code == "NOTE_CREATED"
    assert len(notes.calls) == 1


def test_next_unchanged_collection_reconciles_previous_note_failure(tmp_path):
    notes = FakeNotes(fail_once=True)
    database, repository, adapter, job, _ = build_execution(tmp_path, notes=notes)
    source_context = context_for(repository, job)

    failed = asyncio.run(adapter.execute(source_context))
    reconciled = asyncio.run(adapter.execute(context_for(repository, job)))

    assert failed.status is RunStatus.PARTIAL
    assert reconciled.status is RunStatus.SUCCEEDED
    assert reconciled.outcome_code == "NOTE_RECONCILED"
    assert notes.calls[0][2] == notes.calls[1][2]
    with connect(database) as connection:
        rows = connection.execute(
            "SELECT changed,note_status FROM web_collection_results ORDER BY rowid"
        ).fetchall()
    assert [tuple(row) for row in rows] == [(1, "CREATED"), (0, "NOT_REQUIRED")]


def test_job_application_to_worker_creates_a_real_markdown_note(tmp_path):
    jobs_database = initialize_database(tmp_path / "jobs.db")
    initialize_web_schema(jobs_database)
    notes_database = initialize_notes_database(tmp_path / "notes.db")
    workspace_context = WorkspaceContext(
        user_id="user-a",
        workspace_id="workspace-a",
        membership_id="membership-a",
        role_key="Owner",
        enabled_app_ids=("jobs", "notes"),
        authorization_version=1,
    )
    notes = NotesService(
        notes_database,
        authorization=AllowAuthorization(),  # type: ignore[arg-type]
        clock=lambda: NOW,
    )
    adapter = WebCollectionAdapter(
        execution_enabled=True,
        browser_manager=FakeBrowser(
            "<article><h1>릴리스 공지</h1><ul><li>새 기능</li></ul></article>"
        ),
        database_path=jobs_database,
        notes_service=notes,
        context_resolver=lambda _workspace_id, _actor_id: workspace_context,
        clock=lambda: NOW,
    )
    registry = JobTypeRegistry([adapter])
    repository = JobRepository(jobs_database, registry)
    application = JobApplicationService(repository, registry)

    job = application.create_job(
        workspace_id="workspace-a",
        actor_id="user-a",
        name="릴리스 수집",
        job_type="WEB_COLLECTION",
        schedule=JobSchedule(ScheduleKind.INTERVAL, expression="300"),
        type_config={
            "url": "https://example.com/releases",
            "selector": "article",
            "note_policy": "CHANGED_ONLY",
        },
    )
    tested = asyncio.run(
        application.test_job("workspace-a", job["id"], workspace_context)
    )
    assert tested.success is True
    repository.activate("workspace-a", job["id"], "user-a", now=NOW)
    worker = JobWorker(
        repository,
        registry,
        worker_id="worker-e2e",
        authorize_execution=lambda *_args: True,
    )

    result = asyncio.run(worker.run_due(now=NOW + timedelta(seconds=300)))

    assert result == {
        "recovered": 0,
        "enqueued": 1,
        "claimed": 1,
        "completed": 1,
        "failed": 0,
    }
    run = repository.list_run_details("workspace-a", job["id"])[0]
    assert run["status"] == "SUCCEEDED"
    assert run["outcome_code"] == "NOTE_CREATED"
    assert run["type_result"]["note_id"] == 1
    note = notes.get_note(workspace_context, 1)
    assert note["title"] == "릴리스 수집 - 2026-08-13 13:00:00"
    assert "> 수집 출처: https://example.com/releases" in note["content"]
    assert "# 릴리스 공지" in note["content"]
    assert "- 새 기능" in note["content"]
