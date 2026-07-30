from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from publishing.contracts import PublicationSnapshot
from publishing.registry import (
    PublicationRegistryValidationError,
    PublicationTypeRegistry,
    create_default_registry,
)
from publishing.types.manual import ManualPublicationType
from publishing.types.wiki import WikiPublicationType
from publishing.type_manifests import DeclarativePublicationType


def roots(tmp_path: Path) -> tuple[Path, Path]:
    static = tmp_path / "static"
    templates = tmp_path / "templates"
    (static / "icons").mkdir(parents=True)
    templates.mkdir()
    for name in ("manual", "wiki"):
        (static / "icons" / f"{name}.svg").write_text("<svg/>", encoding="utf-8")
    (static / "icons" / "publication.svg").write_text("<svg/>", encoding="utf-8")
    (templates / "publication_viewer.html").write_text("viewer", encoding="utf-8")
    return static, templates


def registry(tmp_path: Path, *providers) -> PublicationTypeRegistry:
    static, templates = roots(tmp_path)
    return PublicationTypeRegistry(
        list(providers) if providers else [ManualPublicationType(), WikiPublicationType()],
        icon_root=static,
        template_root=templates,
    )


def issue_codes(error: PublicationRegistryValidationError) -> set[str]:
    return {issue.code for issue in error.issues}


def test_default_registry_is_explicit_immutable_and_deterministic(tmp_path: Path) -> None:
    static, templates = roots(tmp_path)
    result = create_default_registry(icon_root=static, template_root=templates)

    assert isinstance(result.get_all(), tuple)
    assert [provider.type_id for provider in result.get_all()] == ["manual", "wiki"]
    assert [provider.type_id for provider in result.get_enabled()] == ["manual", "wiki"]


def test_manual_and_wiki_have_distinct_immutable_render_options(tmp_path: Path) -> None:
    result = registry(tmp_path)
    manual = result.require("manual").render_options()
    wiki = result.require("wiki").render_options()

    assert manual.toc_enabled is True
    assert manual.toc_levels == (2, 3, 4)
    assert manual.related_enabled is False
    assert wiki.toc_enabled is False
    assert wiki.related_enabled is True


def test_optional_extension_hooks_default_to_none(tmp_path: Path) -> None:
    result = registry(tmp_path)

    for provider in result.get_all():
        assert provider.navigation_provider() is None
        assert provider.search_provider() is None
        assert provider.related_content_provider() is None
        assert provider.validate_publish(PublicationSnapshot("T", "S", "Body")) == ()


def test_disabled_type_is_hidden_from_creation_but_still_resolvable(tmp_path: Path) -> None:
    result = registry(tmp_path, replace(ManualPublicationType(), enabled=False))

    assert result.get_creation_types() == ()
    assert result.require("manual").enabled is False
    assert result.resolve_route("/manual/install") is result.require("manual")


def test_route_resolution_honors_segment_boundaries(tmp_path: Path) -> None:
    result = registry(tmp_path)

    assert result.resolve_route("/manual") is result.require("manual")
    assert result.resolve_route("/manual/install") is result.require("manual")
    assert result.resolve_route("/manuals/install") is None
    assert result.resolve_route("/man") is None


def test_validation_collects_collisions_and_unsafe_files(tmp_path: Path) -> None:
    static, templates = roots(tmp_path)
    duplicate = replace(
        WikiPublicationType(),
        type_id="manual",
        route_prefix="/manual",
        order=10,
        icon="/static/icons/missing.svg",
        template_name="../outside.html",
    )

    with pytest.raises(PublicationRegistryValidationError) as caught:
        PublicationTypeRegistry(
            [ManualPublicationType(), duplicate],
            icon_root=static,
            template_root=templates,
        )

    assert {
        "duplicate_type_id",
        "duplicate_route",
        "duplicate_order",
        "missing_or_unsafe_icon",
        "missing_or_unsafe_template",
    } <= issue_codes(caught.value)


def test_validation_collects_missing_hooks(tmp_path: Path) -> None:
    static, templates = roots(tmp_path)

    class BrokenProvider:
        type_id = "broken"
        display_name = "Broken"
        icon = "/static/icons/manual.svg"
        order = 50
        enabled = True
        route_prefix = "/broken"
        template_name = "publication_viewer.html"
        slug_policy = ManualPublicationType().slug_policy

    with pytest.raises(PublicationRegistryValidationError) as caught:
        PublicationTypeRegistry(
            [BrokenProvider()],
            icon_root=static,
            template_root=templates,
        )

    assert issue_codes(caught.value) == {"missing_hook"}


def test_unknown_stored_types_fail_startup_validation(tmp_path: Path) -> None:
    result = registry(tmp_path)

    with pytest.raises(PublicationRegistryValidationError) as caught:
        result.validate_stored_type_ids(["manual", "removed-type"])

    assert issue_codes(caught.value) == {"unknown_stored_type"}


def test_runtime_replacement_is_atomic_and_preserves_registry_identity(
    tmp_path: Path,
) -> None:
    result = registry(tmp_path)
    original_snapshot = result.get_all()
    custom = DeclarativePublicationType(
        type_id="faq",
        display_name="FAQ",
        order=30,
    )

    result.replace_providers(
        [ManualPublicationType(), WikiPublicationType(), custom]
    )

    assert [provider.type_id for provider in result.get_all()] == [
        "manual",
        "wiki",
        "faq",
    ]
    assert result.resolve_route("/publications/view/faq/page") is custom

    with pytest.raises(PublicationRegistryValidationError):
        result.replace_providers(
            [
                ManualPublicationType(),
                replace(WikiPublicationType(), order=10),
            ]
        )

    assert result.get_all() != original_snapshot
    assert [provider.type_id for provider in result.get_all()] == [
        "manual",
        "wiki",
        "faq",
    ]
