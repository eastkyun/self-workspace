from __future__ import annotations

import json
from pathlib import Path
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from access.context import WorkspaceContext
from apps.notes.app.schemas import NoteCreate
from apps.notes.app.service import NotesService
from job_platform.database import connect
from job_platform.errors import JobValidationError

from job_platform.handlers import JobOutcome, JobTestOutcome, JobTypeDescriptor
from job_platform.schemas import RunStatus

from .browser import BrowserManager, BrowserOperationError
from .financial_briefing import (
    DEFAULT_FINANCIAL_FORMATTER_ID,
    FinancialBriefingError,
    FinancialBriefingFormatter,
)
from .markdown import html_to_markdown
from .repository import WebCollectionRepository
from .schemas import OutputFormat, WebCollectionConfig

STATIC_DIR = Path(__file__).resolve().parent / "static"
SEOUL = ZoneInfo("Asia/Seoul")


class WebCollectionAdapter:
    descriptor = JobTypeDescriptor(
        key="WEB_COLLECTION",
        label="Web Collection",
        description="웹페이지 영역을 예약 수집해 Notes로 저장합니다.",
        icon="/static/icons/web-collection.svg",
        editor_key="web-collection",
    )

    def __init__(
        self,
        *,
        execution_enabled: bool = False,
        browser_manager: BrowserManager | None = None,
        database_path: str | Path | None = None,
        notes_service: NotesService | None = None,
        context_resolver: Callable[[str, str], WorkspaceContext] | None = None,
        financial_briefing_formatter: FinancialBriefingFormatter | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.execution_enabled = execution_enabled
        self.browser_manager = browser_manager
        self.database_path = Path(database_path) if database_path is not None else None
        self.notes_service = notes_service
        self.context_resolver = context_resolver
        self.financial_briefing_formatter = financial_briefing_formatter
        self.clock = clock
        self.results = (
            WebCollectionRepository(self.database_path)
            if self.database_path is not None
            else None
        )

    def validate_config(self, payload: object) -> WebCollectionConfig:
        return WebCollectionConfig.parse(payload)

    def create_type_row(self, connection: Any, job: dict[str, Any], config: object) -> None:
        assert isinstance(config, WebCollectionConfig)
        self._validate_formatter_profile(connection, job["workspace_id"], config)
        connection.execute(
            """INSERT INTO web_collection_jobs(
              job_id,workspace_id,url,selector,selector_metadata_json,
              fallback_x,fallback_y,fallback_width,fallback_height,note_policy,output_format,
              formatter_profile_id,extract_links,extract_images,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job["id"], job["workspace_id"], config.url, config.selector,
                config.selector_metadata_json, config.fallback_x, config.fallback_y,
                config.fallback_width, config.fallback_height, config.note_policy.value,
                config.output_format.value, config.formatter_profile_id,
                int(config.extract_links), 0,
                job["created_at"], job["updated_at"],
            ),
        )

    def update_type_row(self, connection: Any, job: dict[str, Any], config: object) -> None:
        assert isinstance(config, WebCollectionConfig)
        self._validate_formatter_profile(connection, job["workspace_id"], config)
        connection.execute(
            """UPDATE web_collection_jobs SET url=?,selector=?,selector_metadata_json=?,
              fallback_x=?,fallback_y=?,fallback_width=?,fallback_height=?,note_policy=?,
              output_format=?,formatter_profile_id=?,extract_links=?,extract_images=0,updated_at=?
              WHERE workspace_id=? AND job_id=?""",
            (
                config.url, config.selector, config.selector_metadata_json, config.fallback_x,
                config.fallback_y, config.fallback_width, config.fallback_height,
                config.note_policy.value, config.output_format.value,
                config.formatter_profile_id,
                int(config.extract_links), job["updated_at"],
                job["workspace_id"], job["id"],
            ),
        )

    def load_type_config(self, connection: Any, job: dict[str, Any]) -> WebCollectionConfig:
        row = connection.execute(
            "SELECT * FROM web_collection_jobs WHERE workspace_id=? AND job_id=?",
            (job["workspace_id"], job["id"]),
        ).fetchone()
        if row is None:
            raise RuntimeError("Web Collection type row is missing")
        return WebCollectionConfig.parse(
            {
                "url": row["url"], "selector": row["selector"],
                "selector_metadata": json.loads(row["selector_metadata_json"]),
                "fallback_x": row["fallback_x"], "fallback_y": row["fallback_y"],
                "fallback_width": row["fallback_width"], "fallback_height": row["fallback_height"],
                "note_policy": row["note_policy"], "output_format": row["output_format"],
                "formatter_profile_id": row["formatter_profile_id"],
                "extract_links": bool(row["extract_links"]),
                "extract_images": False,
            }
        )

    def project_job_detail(self, connection: Any, job: dict[str, Any]) -> dict[str, Any]:
        config = self.load_type_config(connection, job)
        return {
            "url": config.url, "selector": config.selector,
            "selector_metadata": json.loads(config.selector_metadata_json),
            "fallback_x": config.fallback_x, "fallback_y": config.fallback_y,
            "fallback_width": config.fallback_width, "fallback_height": config.fallback_height,
            "note_policy": config.note_policy.value,
            "output_format": config.output_format.value,
            "formatter_profile_id": config.formatter_profile_id,
            "extract_links": config.extract_links,
            "extract_images": False,
        }

    def project_run_detail(self, connection: Any, run: dict[str, Any]) -> dict[str, Any]:
        row = connection.execute(
            "SELECT changed,note_status,note_id,collected_at FROM web_collection_results WHERE workspace_id=? AND run_id=?",
            (run["workspace_id"], run["id"]),
        ).fetchone()
        return dict(row) if row else {}

    async def test(self, context: object, config: object) -> object:
        assert isinstance(config, WebCollectionConfig)
        if not self.execution_enabled or self.browser_manager is None:
            return JobTestOutcome(False, "BROWSER_UNAVAILABLE", {})
        try:
            collected = await self.browser_manager.collect(config.url, config.selector)
            markdown = html_to_markdown(
                collected.content_html, final_url=collected.final_url
            )
            if len(markdown) > 90_000:
                return JobTestOutcome(False, "EXTRACTED_CONTENT_TOO_LARGE", {})
            markdown = await self._format_note_markdown(
                config=config,
                workspace_id=getattr(context, "workspace_id", None),
                source=collected.final_url,
                collected_at=self.clock(),
                raw_content=markdown,
            )
            return JobTestOutcome(
                True,
                "SELECTOR_VERIFIED",
                {
                    "final_url": collected.final_url,
                    "page_title": collected.page_title,
                    "text_preview": collected.content_text[:500],
                    "markdown_preview": markdown[:4_000],
                    "content_hash": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
                },
            )
        except (BrowserOperationError, FinancialBriefingError) as error:
            return JobTestOutcome(False, error.code, {})

    async def preview(self, context: object, payload: object) -> dict[str, Any]:
        if not isinstance(payload, dict) or set(payload) != {"url"}:
            raise JobValidationError("preview requires only a URL")
        url = str(payload.get("url") or "").strip()
        if not 1 <= len(url) <= 2048:
            raise JobValidationError("URL must contain 1-2048 characters")
        if not self.execution_enabled or self.browser_manager is None:
            return {"success": False, "code": "BROWSER_UNAVAILABLE"}
        try:
            preview = await self.browser_manager.preview(url)
        except BrowserOperationError as error:
            return {"success": False, "code": error.code}
        return {
            "success": True,
            "code": "PREVIEW_READY",
            "preview": {
                "final_url": preview.final_url,
                "page_title": preview.page_title,
                "screenshot": f"data:image/jpeg;base64,{preview.screenshot_base64}",
                "width": preview.width,
                "height": preview.height,
                "truncated_x": preview.truncated_x,
                "truncated_y": preview.truncated_y,
                "candidates": list(preview.candidates),
            },
        }

    async def execute(self, context) -> JobOutcome:
        if not self.execution_enabled:
            return JobOutcome(RunStatus.FAILED, "WEB_COLLECTION_DISABLED")
        if (
            self.browser_manager is None
            or self.database_path is None
            or self.results is None
            or self.notes_service is None
            or self.context_resolver is None
        ):
            return JobOutcome(RunStatus.FAILED, "WEB_COLLECTION_UNAVAILABLE")
        with connect(self.database_path) as connection:
            job_row = connection.execute(
                "SELECT * FROM jobs WHERE workspace_id=? AND id=?",
                (context.workspace_id, context.job_id),
            ).fetchone()
            if job_row is None:
                return JobOutcome(RunStatus.FAILED, "JOB_NOT_FOUND")
            job = dict(job_row)
            config = self.load_type_config(connection, job)
        try:
            collected = await self.browser_manager.collect(config.url, config.selector)
            raw_markdown = html_to_markdown(
                collected.content_html, final_url=collected.final_url
            )
            if len(raw_markdown) > 90_000:
                return JobOutcome(RunStatus.FAILED, "EXTRACTED_CONTENT_TOO_LARGE")
        except BrowserOperationError as error:
            return JobOutcome(RunStatus.FAILED, error.code)
        except JobValidationError:
            return JobOutcome(RunStatus.FAILED, "TARGET_POLICY_REJECTED")
        except ValueError:
            return JobOutcome(RunStatus.FAILED, "MARKDOWN_CONVERSION_FAILED")

        digest = hashlib.sha256(raw_markdown.encode("utf-8")).hexdigest()
        previous_result = self.results.latest_result(
            context.workspace_id, context.job_id
        )
        previous_hash = (
            str(previous_result["content_hash"]) if previous_result else None
        )
        changed = previous_hash != digest
        note_required = changed or config.note_policy.value == "EVERY_RUN"
        reconciliation = None
        if (
            not changed
            and config.note_policy.value == "CHANGED_ONLY"
            and previous_result is not None
            and previous_result["note_status"] in {"PENDING", "FAILED"}
        ):
            if previous_result["note_status"] == "FAILED":
                self.results.reopen_failed_note(
                    context.workspace_id,
                    previous_result["run_id"],
                    now=self.clock(),
                )
            reconciliation = self._create_note(
                context=context,
                result_run_id=previous_result["run_id"],
                title=job["name"],
                markdown=previous_result["content_markdown"],
                final_url=previous_result["final_url"],
                add_provenance=config.output_format is OutputFormat.RAW_MARKDOWN,
            )
            note_required = False
        note_markdown = raw_markdown
        if note_required:
            try:
                note_markdown = await self._format_note_markdown(
                    config=config,
                    workspace_id=context.workspace_id,
                    source=collected.final_url,
                    collected_at=self.clock(),
                    raw_content=raw_markdown,
                )
            except FinancialBriefingError as error:
                return JobOutcome(RunStatus.FAILED, error.code)
        self.results.create_result(
            workspace_id=context.workspace_id,
            job_id=context.job_id,
            run_id=context.run_id,
            final_url=collected.final_url,
            content_html=collected.content_html,
            content_text=collected.content_text,
            content_markdown=note_markdown,
            content_hash=digest,
            previous_hash=previous_hash,
            changed=changed,
            note_required=note_required,
            now=self.clock(),
        )
        if not note_required:
            if reconciliation is not None:
                if reconciliation.status is RunStatus.SUCCEEDED:
                    return JobOutcome(RunStatus.SUCCEEDED, "NOTE_RECONCILED")
                return reconciliation
            return JobOutcome(RunStatus.UNCHANGED, "CONTENT_UNCHANGED")
        return self._create_note(
            context=context,
            result_run_id=context.run_id,
            title=job["name"],
            markdown=note_markdown,
            final_url=collected.final_url,
            add_provenance=config.output_format is OutputFormat.RAW_MARKDOWN,
        )

    async def retry(self, context, source_run_id: str) -> JobOutcome:
        if self.results is None:
            return JobOutcome(RunStatus.FAILED, "WEB_COLLECTION_UNAVAILABLE")
        result = self.results.get_for_run(context.workspace_id, source_run_id)
        if result is None:
            return await self.execute(context)
        if result["job_id"] != context.job_id:
            return JobOutcome(RunStatus.FAILED, "SOURCE_RESULT_NOT_FOUND")
        if result["note_status"] == "CREATED":
            return JobOutcome(RunStatus.SUCCEEDED, "NOTE_ALREADY_CREATED")
        if result["note_status"] == "NOT_REQUIRED":
            return JobOutcome(RunStatus.UNCHANGED, "CONTENT_UNCHANGED")
        if result["note_status"] == "FAILED":
            self.results.reopen_failed_note(
                context.workspace_id, source_run_id, now=self.clock()
            )
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE workspace_id=? AND id=?",
                (context.workspace_id, context.job_id),
            ).fetchone()
        if row is None:
            return JobOutcome(RunStatus.FAILED, "JOB_NOT_FOUND")
        job = dict(row)
        with connect(self.database_path) as connection:
            config = self.load_type_config(connection, job)
        return self._create_note(
            context=context,
            result_run_id=source_run_id,
            title=job["name"],
            markdown=result["content_markdown"],
            final_url=result["final_url"],
            add_provenance=config.output_format is OutputFormat.RAW_MARKDOWN,
        )

    async def _format_note_markdown(
        self,
        *,
        config: WebCollectionConfig,
        workspace_id: str | None,
        source: str,
        collected_at: datetime,
        raw_content: str,
    ) -> str:
        if config.output_format is OutputFormat.RAW_MARKDOWN:
            return raw_content
        if self.financial_briefing_formatter is None:
            raise FinancialBriefingError("AI_FORMATTER_UNAVAILABLE")
        instructions = model = None
        required_prefix = "# 📈 주요 금융시장 동향"
        profile_id = config.formatter_profile_id
        if profile_id is not None:
            if self.database_path is None or workspace_id is None:
                raise FinancialBriefingError("AI_FORMATTER_UNAVAILABLE")
        lookup_id = profile_id or DEFAULT_FINANCIAL_FORMATTER_ID
        if self.database_path is not None and workspace_id is not None:
            with connect(self.database_path) as connection:
                profile = connection.execute(
                    """SELECT instructions,model
                       FROM web_collection_formatter_profiles
                       WHERE workspace_id=? AND id=?""",
                    (workspace_id, lookup_id),
                ).fetchone()
            if profile is None and profile_id is not None:
                raise FinancialBriefingError("AI_FORMATTER_PROFILE_NOT_FOUND")
            if profile is not None:
                instructions = profile["instructions"]
                model = profile["model"]
                required_prefix = None
        return await self.financial_briefing_formatter.format(
            source=source,
            collected_at=collected_at.astimezone(SEOUL).strftime(
                "%Y-%m-%d %H:%M:%S KST"
            ),
            raw_content=raw_content,
            instructions=instructions,
            model=model,
            required_prefix=required_prefix,
        )

    @staticmethod
    def _validate_formatter_profile(
        connection: Any, workspace_id: str, config: WebCollectionConfig
    ) -> None:
        if config.formatter_profile_id is None:
            return
        if config.formatter_profile_id == DEFAULT_FINANCIAL_FORMATTER_ID:
            return
        profile = connection.execute(
            """SELECT 1 FROM web_collection_formatter_profiles
               WHERE workspace_id=? AND id=?""",
            (workspace_id, config.formatter_profile_id),
        ).fetchone()
        if profile is None:
            raise JobValidationError("formatter profile is not available")

    def _create_note(
        self,
        *,
        context,
        result_run_id: str,
        title: str,
        markdown: str,
        final_url: str,
        add_provenance: bool = True,
    ) -> JobOutcome:
        assert self.results is not None
        assert self.notes_service is not None
        assert self.context_resolver is not None
        token = self.results.claim_note(
            context.workspace_id, result_run_id, now=self.clock()
        )
        if token is None:
            current = self.results.get_for_run(context.workspace_id, result_run_id)
            if current is not None and current["note_status"] == "CREATED":
                return JobOutcome(RunStatus.SUCCEEDED, "NOTE_ALREADY_CREATED")
            return JobOutcome(RunStatus.PARTIAL, "NOTE_LEASE_BUSY")
        try:
            notes_context = self.context_resolver(
                context.workspace_id, context.actor_id
            )
            provenance = f"> 수집 출처: {final_url}\n\n" if add_provenance else ""
            suffix = f" - {self.clock().astimezone(SEOUL):%Y-%m-%d %H:%M:%S}"
            note_title = f"{title[: 120 - len(suffix)]}{suffix}"
            note = self.notes_service.create_note(
                notes_context,
                NoteCreate(title=note_title, content=provenance + markdown),
                idempotency_key=(
                    f"job-platform:web-collection:run:{result_run_id}"
                ),
            )
        except Exception:
            self.results.fail_note(
                context.workspace_id,
                result_run_id,
                token,
                "NOTE_CREATE_FAILED",
                now=self.clock(),
            )
            return JobOutcome(RunStatus.PARTIAL, "NOTE_CREATE_FAILED")
        if not self.results.complete_note(
            context.workspace_id,
            result_run_id,
            token,
            note["id"],
            now=self.clock(),
        ):
            return JobOutcome(RunStatus.PARTIAL, "NOTE_RESULT_FENCED")
        return JobOutcome(RunStatus.SUCCEEDED, "NOTE_CREATED")

    async def close(self) -> None:
        if self.browser_manager is not None:
            await self.browser_manager.close()
        close_formatter = getattr(self.financial_briefing_formatter, "close", None)
        if close_formatter is not None:
            result = close_formatter()
            if hasattr(result, "__await__"):
                await result
