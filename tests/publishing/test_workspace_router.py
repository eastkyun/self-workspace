from __future__ import annotations

import io

from fastapi.testclient import TestClient
from PIL import Image

from portal.main import create_app
from shared.document_export import DocumentExportService


class FakeDocumentRunner:
    def convert(self, source, output, *, format, resource_path) -> None:
        marker = b"PK\x03\x04" if format == "docx" else b"%PDF-1.7"
        output.write_bytes(marker + source.read_bytes())


def signup(client: TestClient, email: str) -> tuple[str, str]:
    response = client.post(
        "/api/v1/auth/signup",
        headers={"Origin": "http://testserver"},
        json={"email": email, "password": "password"},
    )
    assert response.status_code == 201
    return response.json()["workspace_id"], response.json()["csrf_token"]


def create_note(client: TestClient, workspace_id: str, csrf: str, title: str) -> int:
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/notes",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        json={"title": title, "content": "Workspace publication"},
    )
    assert response.status_code == 201
    return response.json()["id"]


def upload_image(client: TestClient, workspace_id: str, csrf: str) -> dict:
    content = io.BytesIO()
    Image.new("RGB", (2, 2), (10, 20, 30)).save(content, format="PNG")
    response = client.post(
        f"/api/v1/workspaces/{workspace_id}/assets/images",
        params={"upload_session_id": "publication-test"},
        headers={
            "Origin": "http://testserver",
            "X-CSRF-Token": csrf,
            "Content-Type": "image/png",
            "X-File-Name": "published.png",
        },
        content=content.getvalue(),
    )
    assert response.status_code == 201
    return response.json()


def create_publication(
    client: TestClient,
    workspace_id: str,
    csrf: str,
    note_id: int,
    slug: str,
):
    return client.post(
        f"/api/v1/workspaces/{workspace_id}/publications",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        json={"note_id": note_id, "type_id": "manual", "slug": slug},
    )


def test_publications_are_isolated_and_slugs_are_tenant_local(tmp_path):
    app = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
        publishing_database_path=tmp_path / "publishing.db",
        asset_database_path=tmp_path / "assets.db",
    )
    with TestClient(app) as first, TestClient(app) as second:
        unauthenticated = first.get("/apps/publishing/", follow_redirects=False)
        assert unauthenticated.status_code == 307
        assert unauthenticated.headers["location"] == "/login?next=%2Fapps%2Fpublishing%2F"
        workspace_a, csrf_a = signup(first, "publisher-a@example.com")
        workspace_b, csrf_b = signup(second, "publisher-b@example.com")
        note_a = create_note(first, workspace_a, csrf_a, "Private A")
        note_b = create_note(second, workspace_b, csrf_b, "Private B")

        created_a = create_publication(
            first, workspace_a, csrf_a, note_a, "shared-slug"
        )
        assert created_a.status_code == 201
        publication_a = created_a.json()
        assert publication_a["workspace_id"] == workspace_a

        assert second.get(
            f"/api/v1/workspaces/{workspace_b}/publications"
        ).json() == []
        assert second.get(
            f"/api/v1/workspaces/{workspace_b}/publications/{publication_a['id']}"
        ).status_code == 404
        assert second.post(
            f"/api/v1/workspaces/{workspace_b}/publications/{publication_a['id']}/publish",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_b},
        ).status_code == 404

        created_b = create_publication(
            second, workspace_b, csrf_b, note_b, "shared-slug"
        )
        assert created_b.status_code == 201
        assert created_b.json()["workspace_id"] == workspace_b

        published_a = first.post(
            f"/api/v1/workspaces/{workspace_a}/publications/{publication_a['id']}/publish",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_a},
        ).json()
        published_b = second.post(
            f"/api/v1/workspaces/{workspace_b}/publications/{created_b.json()['id']}/publish",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_b},
        ).json()
        workspace_page = first.get(
            f"/w/{workspace_a}/publications", follow_redirects=False
        )
        assert workspace_page.status_code == 200
        assert "Private A" in workspace_page.text
        assert "Private B" not in workspace_page.text
        legacy_menu = first.get("/apps/publishing/", follow_redirects=False)
        assert legacy_menu.status_code == 307
        assert legacy_menu.headers["location"] == f"/w/{workspace_a}/publications"
        resolved_a = first.post(
            f"/api/v1/workspaces/{workspace_a}/internal-links/resolve",
            json={"links": [{"type": "manual", "target": "shared-slug"}]},
        ).json()[0]
        resolved_b = second.post(
            f"/api/v1/workspaces/{workspace_b}/internal-links/resolve",
            json={"links": [{"type": "manual", "target": "shared-slug"}]},
        ).json()[0]
        assert resolved_a["label"] == "Private A"
        assert resolved_a["href"] == f"/p/{published_a['public_id']}/shared-slug"
        assert resolved_b["label"] == "Private B"
        assert resolved_b["href"] == f"/p/{published_b['public_id']}/shared-slug"


def test_public_asset_projection_allows_only_assets_in_published_version(tmp_path):
    app = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
        publishing_database_path=tmp_path / "publishing.db",
        asset_database_path=tmp_path / "assets.db",
    )
    with TestClient(app) as owner, TestClient(app) as other:
        workspace_a, csrf_a = signup(owner, "public-assets-a@example.com")
        workspace_b, csrf_b = signup(other, "public-assets-b@example.com")
        asset_a = upload_image(owner, workspace_a, csrf_a)
        asset_b = upload_image(other, workspace_b, csrf_b)
        note = owner.post(
            f"/api/v1/workspaces/{workspace_a}/notes",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_a},
            json={
                "title": "Published image",
                "content": f"![allowed](image://{asset_a['id']})",
            },
        )
        assert note.status_code == 201
        publication = create_publication(
            owner, workspace_a, csrf_a, note.json()["id"], "public-assets"
        ).json()
        published = owner.post(
            f"/api/v1/workspaces/{workspace_a}/publications/{publication['id']}/publish",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_a},
        )
        assert published.status_code == 200
        public_id = published.json()["public_id"]

        resolved = other.post(
            f"/api/publications/public/{public_id}/assets/resolve",
            json={"ids": [asset_a["id"], asset_b["id"]]},
        )
        assert resolved.status_code == 200
        assert resolved.json()[0]["exists"] is True
        assert resolved.json()[1]["exists"] is False
        assert other.get(resolved.json()[0]["href"]).status_code == 200
        assert other.get(
            f"/api/publications/public/{public_id}/assets/{asset_b['id']}/content"
        ).status_code == 404

        assert owner.patch(
            f"/api/v1/workspaces/{workspace_a}/apps/assets",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_a},
            json={"enabled": False},
        ).status_code == 204
        assert owner.post(
            f"/api/v1/workspaces/{workspace_a}/assets/resolve",
            json={"ids": [asset_a["id"]]},
        ).status_code == 404

        assert owner.patch(
            f"/api/v1/workspaces/{workspace_a}/apps/publishing",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_a},
            json={"enabled": False},
        ).status_code == 204
        assert owner.get(
            f"/api/v1/workspaces/{workspace_a}/publications"
        ).status_code == 404
        assert owner.patch(
            f"/api/v1/workspaces/{workspace_a}/apps/publishing",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_a},
            json={"enabled": True},
        ).status_code == 204
        assert owner.delete(
            f"/api/v1/workspaces/{workspace_a}",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf_a},
        ).status_code == 204
        assert other.get(
            f"/p/{public_id}/public-assets"
        ).status_code == 404
        assert other.post(
            f"/api/publications/public/{public_id}/assets/resolve",
            json={"ids": [asset_a["id"]]},
        ).json()[0]["exists"] is False


def test_note_and_current_publication_version_can_be_exported(tmp_path):
    app = create_app(
        tmp_path / "calendar.db",
        notes_database_path=tmp_path / "notes.db",
        publishing_database_path=tmp_path / "publishing.db",
        asset_database_path=tmp_path / "assets.db",
        document_export_service=DocumentExportService(FakeDocumentRunner()),
    )
    with TestClient(app) as client:
        workspace_id, csrf = signup(client, "exporter@example.com")
        note = client.post(
            f"/api/v1/workspaces/{workspace_id}/notes",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
            json={"title": "한국어 보고서", "content": "# 본문\n\n첫 버전"},
        ).json()

        note_docx = client.get(
            f"/api/v1/workspaces/{workspace_id}/notes/{note['id']}/export",
            params={"format": "docx"},
        )
        assert note_docx.status_code == 200
        assert note_docx.content.startswith(b"PK\x03\x04")
        assert note_docx.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument"
        )
        assert "filename*=UTF-8''" in note_docx.headers["content-disposition"]
        assert client.get(
            f"/api/v1/workspaces/{workspace_id}/notes/{note['id']}/export",
            params={"format": "html"},
        ).status_code == 422

        publication = create_publication(
            client, workspace_id, csrf, note["id"], "export-report"
        ).json()
        published = client.post(
            f"/api/v1/workspaces/{workspace_id}/publications/{publication['id']}/publish",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        ).json()

        public_pdf = client.get(
            f"/api/publications/public/{published['public_id']}/export",
            params={"format": "pdf"},
        )
        assert public_pdf.status_code == 200
        assert public_pdf.content.startswith(b"%PDF-1.7")
        assert public_pdf.headers["content-type"].startswith("application/pdf")

        archived = client.post(
            f"/api/v1/workspaces/{workspace_id}/publications/{publication['id']}/archive",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        )
        assert archived.status_code == 200
        assert client.get(
            f"/api/publications/public/{published['public_id']}/export",
            params={"format": "pdf"},
        ).status_code == 404
