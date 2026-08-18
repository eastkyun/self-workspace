from __future__ import annotations

from inspect import signature
from pathlib import Path

from apps.calendar.app.main import create_calendar_router
from apps.notes.app.main import create_notes_router
from job_platform.router import create_jobs_router
from publishing.router import create_workspace_publishing_router


def test_canonical_workspace_routers_accept_the_shared_effective_app_view():
    for factory in (
        create_notes_router,
        create_calendar_router,
        create_workspace_publishing_router,
        create_jobs_router,
    ):
        assert "effective_apps" in signature(factory).parameters


def test_portal_injects_one_effective_app_service_into_every_workspace_app():
    source = Path("portal/main.py").read_text(encoding="utf-8")
    for factory in (
        "create_notes_router",
        "create_calendar_router",
        "create_workspace_publishing_router",
        "create_jobs_router",
    ):
        call = source[source.index(f"{factory}(") :]
        call = call[: call.index("\n        )")]
        assert "effective_apps=effective_workspace_apps" in call


def test_workspace_home_and_settings_use_the_same_menu_projection():
    source = Path("identity/workspace_shell.py").read_text(encoding="utf-8")
    assert "project_workspace_shell_apps(app_items, workspace_id)" in source
