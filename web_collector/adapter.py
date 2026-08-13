from __future__ import annotations

import json
from pathlib import Path
import hashlib
from typing import Any

from job_platform.handlers import JobOutcome, JobTestOutcome, JobTypeDescriptor
from job_platform.schemas import RunStatus

from .schemas import WebCollectionConfig
from .browser import BrowserManager, BrowserOperationError
from .markdown import html_to_markdown

STATIC_DIR = Path(__file__).resolve().parent / "static"


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
    ) -> None:
        self.execution_enabled = execution_enabled
        self.browser_manager = browser_manager

    def validate_config(self, payload: object) -> WebCollectionConfig:
        return WebCollectionConfig.parse(payload)

    def create_type_row(self, connection: Any, job: dict[str, Any], config: object) -> None:
        assert isinstance(config, WebCollectionConfig)
        connection.execute(
            """INSERT INTO web_collection_jobs(
              job_id,workspace_id,url,selector,selector_metadata_json,
              fallback_x,fallback_y,fallback_width,fallback_height,note_policy,
              extract_links,extract_images,created_at,updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                job["id"], job["workspace_id"], config.url, config.selector,
                config.selector_metadata_json, config.fallback_x, config.fallback_y,
                config.fallback_width, config.fallback_height, config.note_policy.value,
                int(config.extract_links), 0, job["created_at"], job["updated_at"],
            ),
        )

    def update_type_row(self, connection: Any, job: dict[str, Any], config: object) -> None:
        assert isinstance(config, WebCollectionConfig)
        connection.execute(
            """UPDATE web_collection_jobs SET url=?,selector=?,selector_metadata_json=?,
              fallback_x=?,fallback_y=?,fallback_width=?,fallback_height=?,note_policy=?,
              extract_links=?,extract_images=0,updated_at=? WHERE workspace_id=? AND job_id=?""",
            (
                config.url, config.selector, config.selector_metadata_json, config.fallback_x,
                config.fallback_y, config.fallback_width, config.fallback_height,
                config.note_policy.value, int(config.extract_links), job["updated_at"],
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
                "note_policy": row["note_policy"], "extract_links": bool(row["extract_links"]),
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
            "note_policy": config.note_policy.value, "extract_links": config.extract_links,
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
        except BrowserOperationError as error:
            return JobTestOutcome(False, error.code, {})

    async def execute(self, context) -> JobOutcome:
        code = "BROWSER_UNAVAILABLE" if self.execution_enabled else "WEB_COLLECTION_DISABLED"
        return JobOutcome(RunStatus.FAILED, code)

    async def retry(self, context, source_run_id: str) -> JobOutcome:
        return await self.execute(context)

    async def close(self) -> None:
        if self.browser_manager is not None:
            await self.browser_manager.close()
