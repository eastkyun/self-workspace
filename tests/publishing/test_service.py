from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from access.context import WorkspaceContext
from publishing.database import (
    database_connection,
    initialize_database,
    verify_pointer_invariants,
    verify_projection_source_of_truth,
)
from publishing.events import AfterCommitEventDispatcher, PublicationPublished
from publishing.registry import PublicationTypeRegistry
from publishing.repository import (
    get_publication,
    get_publication_version,
    list_publication_versions,
)
from publishing.service import (
    DuplicateSlugError,
    InvalidPublicationTransitionError,
    InvalidSlugError,
    PublicationNotFoundError,
    PublicationTypeDisabledError,
    PublicationTypeNotFoundError,
    PublicationValidationError,
    PublicationVersionNotFoundError,
    PublishingService,
    SourceNoteNotFoundError,
)
from publishing.source_notes import NoteSnapshot, NotesServiceSnapshotReader
from publishing.types.manual import ManualPublicationType
from publishing.types.wiki import WikiPublicationType

NOW = datetime(2026, 7, 30, tzinfo=UTC)
CONTEXT = WorkspaceContext(
    user_id="00000000-0000-4000-8000-000000000001",
    workspace_id="00000000-0000-4000-8000-000000000002",
    membership_id="00000000-0000-4000-8000-000000000003",
    role_key="owner",
    enabled_app_ids=("notes",),
    authorization_version=1,
)


class FakeNotes:
    def __init__(self) -> None:
        self.notes = {
            13: NoteSnapshot(13, "Install", "# Install", "2026-07-30T00:00:00Z")
        }

    def get_snapshot(self, note_id: int) -> NoteSnapshot | None:
        return self.notes.get(note_id)


def make_registry(tmp_path: Path, *providers) -> PublicationTypeRegistry:
    static = tmp_path / "static"
    templates = tmp_path / "templates"
    (static / "icons").mkdir(parents=True)
    templates.mkdir()
    for name in ("manual", "wiki"):
        (static / "icons" / f"{name}.svg").write_text("<svg/>", encoding="utf-8")
    (templates / "publication_viewer.html").write_text("viewer", encoding="utf-8")
    return PublicationTypeRegistry(
        list(providers) if providers else [ManualPublicationType(), WikiPublicationType()],
        icon_root=static,
        template_root=templates,
    )


def make_service(
    tmp_path: Path,
    *,
    notes: FakeNotes | None = None,
    providers=(),
    dispatcher: AfterCommitEventDispatcher | None = None,
) -> tuple[PublishingService, Path, FakeNotes]:
    path = initialize_database(tmp_path / "publishing.db")
    source = notes or FakeNotes()
    return (
        PublishingService(
            path,
            registry=make_registry(tmp_path, *providers),
            note_reader=source,
            clock=lambda: NOW,
            event_dispatcher=dispatcher,
        ),
        path,
        source,
    )


def assert_valid(path: Path) -> None:
    with database_connection(path) as connection:
        verify_pointer_invariants(connection)
        verify_projection_source_of_truth(connection)


def test_notes_service_adapter_reads_saved_state_in_process() -> None:
    class FakeNotesService:
        def get_note(self, context: WorkspaceContext, note_id: int):
            assert context is CONTEXT
            if note_id != 13:
                raise LookupError(note_id)
            return {
                "id": 13,
                "title": "Saved",
                "content": "# Saved",
                "updated_at": "2026-07-30T00:00:00Z",
            }

    reader = NotesServiceSnapshotReader(FakeNotesService(), CONTEXT)

    assert reader.get_snapshot(13) == NoteSnapshot(
        13, "Saved", "# Saved", "2026-07-30T00:00:00Z"
    )
    assert reader.get_snapshot(404) is None


def test_note_is_copied_as_immutable_snapshot_and_does_not_live_update(
    tmp_path: Path,
) -> None:
    service, path, notes = make_service(tmp_path)
    publication = service.create_draft_from_note(
        note_id=13, type_id="manual", slug="install", summary="First"
    )
    notes.notes[13] = NoteSnapshot(
        13, "Changed Note", "# Changed", "2026-07-30T01:00:00Z"
    )

    with database_connection(path) as connection:
        assert get_publication_version(connection, publication["id"], 1) == {
            "id": 1,
            "publication_id": publication["id"],
            "version": 1,
            "title": "Install",
            "summary": "First",
            "markdown": "# Install",
            "source_note_updated_at": "2026-07-30T00:00:00Z",
            "created_at": "2026-07-30T00:00:00Z",
        }
    updated = service.save_draft_from_note(publication["id"], summary="Second")
    assert updated["latest_version"] == 2
    assert updated["draft_version"] == 2
    assert updated["title"] == "Changed Note"
    assert updated["summary"] == "Second"
    assert_valid(path)


def test_published_and_working_draft_coexist_and_public_read_uses_published(
    tmp_path: Path,
) -> None:
    service, path, notes = make_service(tmp_path)
    publication = service.create_draft_from_note(
        note_id=13, type_id="manual", slug="install"
    )
    service.publish(publication["id"])
    notes.notes[13] = NoteSnapshot(
        13, "Draft title", "# Draft", "2026-07-30T02:00:00Z"
    )
    updated = service.save_draft_from_note(publication["id"], summary="draft summary")

    assert updated["status"] == "published"
    assert updated["published_version"] == 1
    assert updated["draft_version"] == 2
    assert updated["latest_version"] == 2
    assert updated["title"] == "Draft title"
    public = service.get_public_content(publication_id=publication["id"])
    assert public.version["version"] == 1
    assert public.version["title"] == "Install"

    republished = service.publish(publication["id"])
    assert republished["published_version"] == 2
    assert republished["draft_version"] is None
    assert service.get_public_content(
        type_id="manual", slug="install"
    ).version["title"] == "Draft title"
    assert_valid(path)


def test_discard_archive_restore_and_version_restore_preserve_history(
    tmp_path: Path,
) -> None:
    service, path, notes = make_service(tmp_path)
    publication = service.create_draft_from_note(
        note_id=13, type_id="manual", slug="install"
    )
    service.publish(publication["id"])
    notes.notes[13] = NoteSnapshot(
        13, "Second", "# Second", "2026-07-30T02:00:00Z"
    )
    service.save_draft_from_note(publication["id"])
    discarded = service.discard_draft(publication["id"])
    assert discarded["draft_version"] is None
    assert discarded["title"] == "Install"

    archived = service.archive(publication["id"])
    assert archived["status"] == "archived"
    with pytest.raises(PublicationNotFoundError):
        service.get_public_content(publication_id=publication["id"])
    restored = service.restore(publication["id"])
    assert restored["status"] == "published"

    new_draft = service.restore_version_as_draft(publication["id"], 1)
    assert new_draft["latest_version"] == 3
    assert new_draft["draft_version"] == 3
    with database_connection(path) as connection:
        assert [row["version"] for row in list_publication_versions(connection, publication["id"])] == [3, 2, 1]
        assert get_publication_version(connection, publication["id"], 3)["markdown"] == "# Install"
    assert_valid(path)


def test_unpublished_discard_clears_pointer_and_restore_copies_snapshot(
    tmp_path: Path,
) -> None:
    service, path, _ = make_service(tmp_path)
    publication = service.create_draft_from_note(
        note_id=13, type_id="wiki", slug="install"
    )
    discarded = service.discard_draft(publication["id"])
    assert discarded["status"] == "archived"
    assert discarded["draft_version"] is None
    restored = service.restore(publication["id"])
    assert restored["status"] == "draft"
    assert restored["latest_version"] == 2
    assert restored["draft_version"] == 2
    with database_connection(path) as connection:
        assert get_publication_version(connection, publication["id"], 1)["markdown"] == "# Install"
        assert get_publication_version(connection, publication["id"], 2)["markdown"] == "# Install"
    assert_valid(path)


def test_same_note_and_type_can_have_multiple_publications(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)

    first = service.create_draft_from_note(
        note_id=13, type_id="wiki", slug="install"
    )
    second = service.create_draft_from_note(
        note_id=13, type_id="wiki", slug="setup-history"
    )

    assert first["id"] != second["id"]
    assert first["note_id"] == second["note_id"] == 13


@pytest.mark.parametrize(
    "slug",
    ("Install", "install_guide", "-install", "install--guide", "한글", "api", "x" * 81),
)
def test_canonical_slug_policy_is_enforced(tmp_path: Path, slug: str) -> None:
    service, _, _ = make_service(tmp_path)
    with pytest.raises(InvalidSlugError):
        service.create_draft_from_note(note_id=13, type_id="manual", slug=slug)


def test_duplicate_slug_is_explicit_domain_error(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    service.create_draft_from_note(note_id=13, type_id="manual", slug="install")
    with pytest.raises(DuplicateSlugError):
        service.create_draft_from_note(note_id=13, type_id="manual", slug="install")


def test_source_type_and_transition_errors_are_explicit(tmp_path: Path) -> None:
    service, _, _ = make_service(tmp_path)
    with pytest.raises(SourceNoteNotFoundError):
        service.create_draft_from_note(note_id=404, type_id="manual", slug="missing")
    with pytest.raises(PublicationTypeNotFoundError):
        service.create_draft_from_note(note_id=13, type_id="missing", slug="missing")

    disabled, _, _ = make_service(
        tmp_path / "disabled",
        providers=(replace(ManualPublicationType(), enabled=False),),
    )
    with pytest.raises(PublicationTypeDisabledError):
        disabled.create_draft_from_note(note_id=13, type_id="manual", slug="install")

    publication = service.create_draft_from_note(
        note_id=13, type_id="manual", slug="install"
    )
    service.publish(publication["id"])
    with pytest.raises(InvalidPublicationTransitionError):
        service.publish(publication["id"])
    with pytest.raises(PublicationVersionNotFoundError):
        service.restore_version_as_draft(publication["id"], 99)


def test_provider_validation_rolls_back_publish(tmp_path: Path) -> None:
    class RejectingManual(ManualPublicationType):
        def validate_publish(self, snapshot):
            return ("title rejected",)

    service, path, _ = make_service(tmp_path, providers=(RejectingManual(),))
    publication = service.create_draft_from_note(
        note_id=13, type_id="manual", slug="install"
    )

    with pytest.raises(PublicationValidationError) as caught:
        service.publish(publication["id"])
    assert caught.value.issues == ("title rejected",)
    with database_connection(path) as connection:
        stored = get_publication(connection, publication["id"])
        assert stored["status"] == "draft"
        assert stored["published_version"] is None


def test_events_run_after_commit_and_subscriber_failure_does_not_rollback(
    tmp_path: Path,
) -> None:
    observed: list[tuple[type, bool]] = []
    path = tmp_path / "publishing.db"

    def observer(event) -> None:
        if isinstance(event, PublicationPublished):
            with database_connection(path) as connection:
                row = get_publication(connection, event.publication_id)
                observed.append((type(event), row["published_version"] == event.version))

    def broken(event) -> None:
        raise RuntimeError("ignored after commit")

    service, _, _ = make_service(
        tmp_path,
        dispatcher=AfterCommitEventDispatcher((broken, observer)),
    )
    publication = service.create_draft_from_note(
        note_id=13, type_id="manual", slug="install"
    )
    service.publish(publication["id"])

    assert observed == [(PublicationPublished, True)]
    assert service.get_public_content(publication_id=publication["id"]).version["version"] == 1
