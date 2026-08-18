from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from job_platform.database import connect, transaction
from job_platform.errors import JobConflict, JobNotFound, JobValidationError
from job_platform.repository import timestamp
from web_collector.financial_briefing import (
    DEFAULT_FINANCIAL_FORMATTER_ID,
    DEFAULT_OPENAI_MODEL,
    default_financial_instructions,
)

MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}$")


def _profile_values(name: str, instructions: str, model: str) -> tuple[str, str, str]:
    clean_name = str(name).strip()
    clean_instructions = str(instructions).strip()
    clean_model = str(model).strip()
    if not 1 <= len(clean_name) <= 80:
        raise JobValidationError("formatter name must contain 1-80 characters")
    if not 1 <= len(clean_instructions) <= 30_000:
        raise JobValidationError("formatter instructions must contain 1-30000 characters")
    if "\x00" in clean_name or "\x00" in clean_instructions:
        raise JobValidationError("formatter text must not contain a NUL byte")
    if not MODEL_NAME.fullmatch(clean_model):
        raise JobValidationError("formatter model contains unsupported characters")
    return clean_name, clean_instructions, clean_model


class FormatterProfileRepository:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)

    def list(self, workspace_id: str) -> list[dict]:
        with connect(self.database_path) as connection:
            rows = connection.execute(
                """SELECT p.*,
                   (SELECT COUNT(*) FROM web_collection_jobs j
                    WHERE j.workspace_id=p.workspace_id
                      AND j.formatter_profile_id=p.id) AS use_count
                   FROM web_collection_formatter_profiles p
                   WHERE p.workspace_id=? AND p.id!=?
                   ORDER BY p.name COLLATE NOCASE,p.id""",
                (workspace_id, DEFAULT_FINANCIAL_FORMATTER_ID),
            ).fetchall()
            builtin = self._builtin(connection, workspace_id)
        return [builtin, *[self._custom(dict(row)) for row in rows]]

    def get(self, workspace_id: str, profile_id: str) -> dict:
        with connect(self.database_path) as connection:
            if profile_id == DEFAULT_FINANCIAL_FORMATTER_ID:
                return self._builtin(connection, workspace_id)
            row = connection.execute(
                """SELECT p.*,
                   (SELECT COUNT(*) FROM web_collection_jobs j
                    WHERE j.workspace_id=p.workspace_id
                      AND j.formatter_profile_id=p.id) AS use_count
                   FROM web_collection_formatter_profiles p
                   WHERE p.workspace_id=? AND p.id=?""",
                (workspace_id, profile_id),
            ).fetchone()
        if row is None:
            raise JobNotFound("formatter profile not found")
        return self._custom(dict(row))

    def create(
        self,
        workspace_id: str,
        actor_id: str,
        *,
        name: str,
        instructions: str,
        model: str,
        now: datetime | None = None,
    ) -> dict:
        clean_name, clean_instructions, clean_model = _profile_values(
            name, instructions, model
        )
        profile_id = str(uuid4())
        stamp = timestamp(now or datetime.now(UTC))
        try:
            with transaction(self.database_path, immediate=True) as connection:
                connection.execute(
                    """INSERT INTO web_collection_formatter_profiles(
                       id,workspace_id,name,instructions,model,revision,
                       created_by,updated_by,created_at,updated_at
                       ) VALUES(?,?,?,?,?,1,?,?,?,?)""",
                    (
                        profile_id,
                        workspace_id,
                        clean_name,
                        clean_instructions,
                        clean_model,
                        actor_id,
                        actor_id,
                        stamp,
                        stamp,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise JobConflict("formatter name already exists") from error
        return self.get(workspace_id, profile_id)

    def update(
        self,
        workspace_id: str,
        profile_id: str,
        actor_id: str,
        *,
        name: str,
        instructions: str,
        model: str,
        expected_revision: int,
        now: datetime | None = None,
    ) -> dict:
        clean_name, clean_instructions, clean_model = _profile_values(
            name, instructions, model
        )
        if expected_revision < 1:
            raise JobValidationError("formatter revision must be positive")
        stamp = timestamp(now or datetime.now(UTC))
        try:
            with transaction(self.database_path, immediate=True) as connection:
                current = connection.execute(
                    """SELECT * FROM web_collection_formatter_profiles
                       WHERE workspace_id=? AND id=?""",
                    (workspace_id, profile_id),
                ).fetchone()
                builtin = profile_id == DEFAULT_FINANCIAL_FORMATTER_ID
                if current is None and not builtin:
                    raise JobNotFound("formatter profile not found")
                current_revision = current["revision"] if current is not None else 1
                if current_revision != expected_revision:
                    raise JobConflict("formatter profile changed concurrently")
                if self._has_running_job(connection, workspace_id, profile_id):
                    raise JobConflict("formatter profile has a running Job")
                if current is None:
                    connection.execute(
                        """INSERT INTO web_collection_formatter_profiles(
                           id,workspace_id,name,instructions,model,revision,
                           created_by,updated_by,created_at,updated_at
                           ) VALUES(?,?,?,?,?,2,?,?,?,?)""",
                        (
                            profile_id,
                            workspace_id,
                            clean_name,
                            clean_instructions,
                            clean_model,
                            actor_id,
                            actor_id,
                            stamp,
                            stamp,
                        ),
                    )
                else:
                    updated = connection.execute(
                        """UPDATE web_collection_formatter_profiles
                           SET name=?,instructions=?,model=?,revision=revision+1,
                               updated_by=?,updated_at=?
                           WHERE workspace_id=? AND id=? AND revision=?""",
                        (
                            clean_name,
                            clean_instructions,
                            clean_model,
                            actor_id,
                            stamp,
                            workspace_id,
                            profile_id,
                            expected_revision,
                        ),
                    ).rowcount
                    if updated != 1:
                        raise JobConflict("formatter profile changed concurrently")
                self._invalidate_jobs(
                    connection, workspace_id, profile_id, actor_id, stamp
                )
        except sqlite3.IntegrityError as error:
            raise JobConflict("formatter name already exists") from error
        return self.get(workspace_id, profile_id)

    def delete(
        self, workspace_id: str, profile_id: str, actor_id: str = "system"
    ) -> dict:
        with transaction(self.database_path, immediate=True) as connection:
            profile = connection.execute(
                """SELECT * FROM web_collection_formatter_profiles
                   WHERE workspace_id=? AND id=?""",
                (workspace_id, profile_id),
            ).fetchone()
            if profile_id == DEFAULT_FINANCIAL_FORMATTER_ID:
                if profile is None:
                    return {
                        "deleted": False,
                        "reset": False,
                        "id": profile_id,
                    }
                if self._has_running_job(connection, workspace_id, profile_id):
                    raise JobConflict("formatter profile has a running Job")
                connection.execute(
                    """DELETE FROM web_collection_formatter_profiles
                       WHERE workspace_id=? AND id=?""",
                    (workspace_id, profile_id),
                )
                self._invalidate_jobs(
                    connection,
                    workspace_id,
                    profile_id,
                    actor_id,
                    timestamp(datetime.now(UTC)),
                )
                return {"deleted": False, "reset": True, "id": profile_id}
            if profile is None:
                raise JobNotFound("formatter profile not found")
            in_use = connection.execute(
                """SELECT COUNT(*) FROM web_collection_jobs
                   WHERE workspace_id=? AND formatter_profile_id=?""",
                (workspace_id, profile_id),
            ).fetchone()[0]
            if in_use:
                raise JobConflict("formatter profile is used by a Job")
            connection.execute(
                """DELETE FROM web_collection_formatter_profiles
                   WHERE workspace_id=? AND id=?""",
                (workspace_id, profile_id),
            )
        return {"deleted": True, "id": profile_id}

    @staticmethod
    def _custom(profile: dict) -> dict:
        return {**profile, "is_builtin": False, "is_overridden": False}

    @staticmethod
    def _builtin(connection, workspace_id: str) -> dict:
        row = connection.execute(
            """SELECT * FROM web_collection_formatter_profiles
               WHERE workspace_id=? AND id=?""",
            (workspace_id, DEFAULT_FINANCIAL_FORMATTER_ID),
        ).fetchone()
        use_count = connection.execute(
            """SELECT COUNT(*) FROM web_collection_jobs
               WHERE workspace_id=?
                 AND output_format='FINANCIAL_MARKET_BRIEFING'
                 AND (formatter_profile_id IS NULL OR formatter_profile_id=?)""",
            (workspace_id, DEFAULT_FINANCIAL_FORMATTER_ID),
        ).fetchone()[0]
        if row is not None:
            return {
                **dict(row),
                "use_count": use_count,
                "is_builtin": True,
                "is_overridden": True,
            }
        return {
            "id": DEFAULT_FINANCIAL_FORMATTER_ID,
            "workspace_id": workspace_id,
            "name": "AI 금융시장 브리핑",
            "instructions": default_financial_instructions(),
            "model": DEFAULT_OPENAI_MODEL,
            "revision": 1,
            "created_by": "system",
            "updated_by": "system",
            "created_at": None,
            "updated_at": None,
            "use_count": use_count,
            "is_builtin": True,
            "is_overridden": False,
        }

    @staticmethod
    def _has_running_job(connection, workspace_id: str, profile_id: str) -> bool:
        if profile_id == DEFAULT_FINANCIAL_FORMATTER_ID:
            condition = (
                "j.output_format='FINANCIAL_MARKET_BRIEFING' "
                "AND (j.formatter_profile_id IS NULL OR j.formatter_profile_id=?)"
            )
        else:
            condition = "j.formatter_profile_id=?"
        row = connection.execute(
            f"""SELECT 1 FROM job_runs r
                JOIN web_collection_jobs j
                  ON j.workspace_id=r.workspace_id AND j.job_id=r.job_id
                WHERE j.workspace_id=? AND {condition}
                  AND r.status='RUNNING' LIMIT 1""",
            (workspace_id, profile_id),
        ).fetchone()
        return row is not None

    @staticmethod
    def _invalidate_jobs(
        connection,
        workspace_id: str,
        profile_id: str,
        actor_id: str,
        stamp: str,
    ) -> None:
        if profile_id == DEFAULT_FINANCIAL_FORMATTER_ID:
            condition = (
                "output_format='FINANCIAL_MARKET_BRIEFING' "
                "AND (formatter_profile_id IS NULL OR formatter_profile_id=?)"
            )
        else:
            condition = "formatter_profile_id=?"
        connection.execute(
            f"""UPDATE jobs SET
                status=CASE WHEN status='ACTIVE' THEN 'PAUSED' ELSE status END,
                next_run_at=NULL,
                definition_revision=definition_revision+1,
                last_tested_revision=NULL,last_tested_at=NULL,
                updated_by=?,updated_at=?
                WHERE workspace_id=? AND status!='ARCHIVED' AND id IN (
                  SELECT job_id FROM web_collection_jobs
                  WHERE workspace_id=? AND {condition}
                )""",
            (actor_id, stamp, workspace_id, workspace_id, profile_id),
        )


class WebCollectionRepository:
    def __init__(self, database_path: str | Path, *, note_lease_seconds: int = 30) -> None:
        self.database_path = Path(database_path)
        self.note_lease_seconds = note_lease_seconds

    def latest_hash(self, workspace_id: str, job_id: str) -> str | None:
        result = self.latest_result(workspace_id, job_id)
        return str(result["content_hash"]) if result else None

    def latest_result(self, workspace_id: str, job_id: str) -> dict | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                """SELECT * FROM web_collection_results
                WHERE workspace_id=? AND job_id=?
                ORDER BY collected_at DESC,rowid DESC LIMIT 1""",
                (workspace_id, job_id),
            ).fetchone()
        return dict(row) if row else None

    def create_result(
        self,
        *,
        workspace_id: str,
        job_id: str,
        run_id: str,
        final_url: str,
        content_html: str,
        content_text: str,
        content_markdown: str,
        content_hash: str,
        previous_hash: str | None,
        changed: bool,
        note_required: bool,
        now: datetime | None = None,
    ) -> dict:
        stamp = timestamp(now or datetime.now(UTC))
        with transaction(self.database_path, immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM web_collection_results WHERE workspace_id=? AND run_id=?",
                (workspace_id, run_id),
            ).fetchone()
            if existing is not None:
                return dict(existing)
            result_id = str(uuid4())
            connection.execute(
                """INSERT INTO web_collection_results(
                id,workspace_id,job_id,run_id,collected_at,final_url,
                content_html,content_text,content_markdown,content_hash,
                previous_hash,changed,note_status,note_id,note_token,
                note_lease_until,error_code,error_message,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL,NULL,NULL,NULL,?,?)""",
                (
                    result_id, workspace_id, job_id, run_id, stamp, final_url,
                    content_html, content_text, content_markdown, content_hash,
                    previous_hash, int(changed),
                    "PENDING" if note_required else "NOT_REQUIRED", stamp, stamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM web_collection_results WHERE id=?", (result_id,)
            ).fetchone()
            return dict(row)

    def get_for_run(self, workspace_id: str, run_id: str) -> dict | None:
        with connect(self.database_path) as connection:
            row = connection.execute(
                "SELECT * FROM web_collection_results WHERE workspace_id=? AND run_id=?",
                (workspace_id, run_id),
            ).fetchone()
        return dict(row) if row else None

    def claim_note(self, workspace_id: str, run_id: str, *, now: datetime | None = None) -> str | None:
        instant = now or datetime.now(UTC)
        stamp = timestamp(instant)
        token = str(uuid4())
        lease_until = timestamp(instant + timedelta(seconds=self.note_lease_seconds))
        with transaction(self.database_path, immediate=True) as connection:
            updated = connection.execute(
                """UPDATE web_collection_results
                SET note_token=?,note_lease_until=?,updated_at=?
                WHERE workspace_id=? AND run_id=? AND note_status='PENDING'
                  AND (note_token IS NULL OR note_lease_until<=?)""",
                (token, lease_until, stamp, workspace_id, run_id, stamp),
            )
            return token if updated.rowcount == 1 else None

    def reopen_failed_note(
        self, workspace_id: str, run_id: str, *, now: datetime | None = None
    ) -> bool:
        with transaction(self.database_path, immediate=True) as connection:
            updated = connection.execute(
                """UPDATE web_collection_results
                SET note_status='PENDING',error_code=NULL,error_message=NULL,updated_at=?
                WHERE workspace_id=? AND run_id=? AND note_status='FAILED'""",
                (timestamp(now or datetime.now(UTC)), workspace_id, run_id),
            )
            return updated.rowcount == 1

    def complete_note(
        self,
        workspace_id: str,
        run_id: str,
        token: str,
        note_id: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        with transaction(self.database_path, immediate=True) as connection:
            updated = connection.execute(
                """UPDATE web_collection_results
                SET note_status='CREATED',note_id=?,note_token=NULL,
                    note_lease_until=NULL,updated_at=?
                WHERE workspace_id=? AND run_id=? AND note_status='PENDING'
                  AND note_token=?""",
                (note_id, timestamp(now or datetime.now(UTC)), workspace_id, run_id, token),
            )
            return updated.rowcount == 1

    def fail_note(
        self,
        workspace_id: str,
        run_id: str,
        token: str,
        code: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        with transaction(self.database_path, immediate=True) as connection:
            updated = connection.execute(
                """UPDATE web_collection_results
                SET note_status='FAILED',note_token=NULL,note_lease_until=NULL,
                    error_code=?,error_message='Note creation failed',updated_at=?
                WHERE workspace_id=? AND run_id=? AND note_status='PENDING'
                  AND note_token=?""",
                (code[:64], timestamp(now or datetime.now(UTC)), workspace_id, run_id, token),
            )
            return updated.rowcount == 1
