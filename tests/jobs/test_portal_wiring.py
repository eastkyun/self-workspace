from job_platform.database import initialize_database
from apps.notes.app.database import initialize_database as initialize_notes_database
from portal.main import create_app
from web_collector.database import initialize_database as initialize_web_collection_database
from workspace_platform.app_manifests import load_builtin_manifests


def test_portal_wires_jobs_only_when_explicitly_enabled(tmp_path, monkeypatch):
    jobs_database = initialize_database(tmp_path / "jobs.db")
    initialize_web_collection_database(jobs_database)
    initialize_notes_database(tmp_path / "notes.db")
    monkeypatch.setenv("JOBS_ENABLED", "true")
    monkeypatch.setenv("JOBS_WEB_COLLECTION_ENABLED", "false")
    app = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
        publishing_database_path=tmp_path / "publishing.db",
        asset_database_path=tmp_path / "assets.db",
        identity_database_path=tmp_path / "identity.db",
        jobs_database_path=jobs_database,
    )
    paths = {route.path for route in app.routes}
    assert "/w/{workspace_id}/jobs" in paths
    assert "/api/v1/workspaces/{workspace_id}/jobs" in paths
    assert "/api/v1/workspaces/{workspace_id}/jobs/formatters" in paths
    assert "/api/v1/workspaces/{workspace_id}/jobs/{job_id}/run" in paths
    assert app.state.job_runtime is not None


def test_jobs_manifest_is_single_fail_closed_workspace_app():
    jobs = next(item for item in load_builtin_manifests() if item.key == "jobs")
    assert jobs.routes.ui == "/w/{workspace_id}/jobs"
    assert jobs.routes.api_v1 == "/api/v1/workspaces/{workspace_id}/jobs"
    assert jobs.default_enabled.personal is False
    assert jobs.default_enabled.team is False
    assert {permission.code for permission in jobs.permissions} == {
        "jobs.read_all", "jobs.create", "jobs.update_own", "jobs.update_all",
        "jobs.delete_own", "jobs.delete_all", "jobs.manage",
    }
