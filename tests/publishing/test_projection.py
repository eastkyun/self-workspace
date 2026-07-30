from pathlib import Path

from publishing.database import initialize_database
from publishing.projection import build_portal_projection
from publishing.registry import PublicationTypeRegistry
from publishing.service import PublishingService
from publishing.source_notes import NoteSnapshot
from publishing.types.manual import ManualPublicationType
from publishing.types.wiki import WikiPublicationType


class Notes:
    def __init__(self) -> None:
        self.note = NoteSnapshot(1, "Published", "# v1", "2026-07-30T00:00:00Z")

    def get_snapshot(self, note_id: int):
        return self.note if note_id == 1 else None


def registry(tmp_path: Path) -> PublicationTypeRegistry:
    icons = tmp_path / "static" / "icons"
    templates = tmp_path / "templates"
    icons.mkdir(parents=True)
    templates.mkdir()
    for name in ("manual", "wiki"):
        (icons / f"{name}.svg").write_text("<svg/>", encoding="utf-8")
    (templates / "publication_viewer.html").write_text("", encoding="utf-8")
    return PublicationTypeRegistry(
        [ManualPublicationType(), WikiPublicationType()],
        icon_root=tmp_path / "static",
        template_root=templates,
    )


def test_projection_uses_public_snapshot_not_newer_working_draft(
    tmp_path: Path,
) -> None:
    database = initialize_database(tmp_path / "publishing.db")
    types = registry(tmp_path)
    notes = Notes()
    service = PublishingService(database, registry=types, note_reader=notes)
    publication = service.create_draft_from_note(
        note_id=1,
        type_id="manual",
        slug="published",
        summary="Public summary",
    )
    service.publish(publication["id"])
    notes.note = NoteSnapshot(1, "Private draft", "# v2", "2026-07-30T01:00:00Z")
    service.save_draft_from_note(publication["id"], summary="Private summary")

    result = build_portal_projection(database, types)

    manual = result["groups"][0]["items"][0]
    assert manual["title"] == "Published"
    assert manual["summary"] == "Public summary"
    assert manual["version"] == 1
    assert manual["href"] == "/manual/published"
    assert result["groups"][1]["items"] == ()


def test_projection_excludes_draft_and_archived_publications(tmp_path: Path) -> None:
    database = initialize_database(tmp_path / "publishing.db")
    types = registry(tmp_path)
    service = PublishingService(database, registry=types, note_reader=Notes())
    draft = service.create_draft_from_note(
        note_id=1, type_id="manual", slug="draft"
    )
    archived = service.create_draft_from_note(
        note_id=1, type_id="wiki", slug="archived"
    )
    service.publish(archived["id"])
    service.archive(archived["id"])

    result = build_portal_projection(database, types)

    assert result["recent"] == ()
    assert draft["id"] > 0
