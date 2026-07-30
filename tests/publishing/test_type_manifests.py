from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from publishing.type_manifests import (
    DeclarativePublicationType,
    PublicationTypeDefinition,
    PublicationTypeManifestStore,
)


def definition(
    type_id: str = "faq",
    *,
    display_name: str = "FAQ",
    order: int = 30,
) -> PublicationTypeDefinition:
    return PublicationTypeDefinition(
        type_id=type_id,
        display_name=display_name,
        order=order,
        toc_enabled=False,
        related_enabled=True,
    )


def test_missing_manifest_is_an_empty_immutable_snapshot(tmp_path: Path) -> None:
    store = PublicationTypeManifestStore(tmp_path / "types.json")

    assert store.get_all() == ()
    assert store.providers() == ()


def test_manifest_crud_is_deterministic_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "types.json"
    store = PublicationTypeManifestStore(path)

    store.save(definition("release-note", order=40), replace=False)
    store.save(definition("faq", order=30), replace=False)

    assert [item.type_id for item in store.get_all()] == ["faq", "release-note"]
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert [item["type_id"] for item in document["types"]] == [
        "faq",
        "release-note",
    ]
    assert not list(path.parent.glob(".publication-types-*.json"))

    updated = definition("faq", display_name="Frequently Asked Questions", order=35)
    store.save(updated, replace=True)
    assert store.get("faq") == updated

    store.delete("release-note")
    assert [item.type_id for item in store.get_all()] == ["faq"]


def test_manifest_store_rejects_invalid_operations_and_schema(tmp_path: Path) -> None:
    path = tmp_path / "types.json"
    store = PublicationTypeManifestStore(path)
    faq = definition()

    store.save(faq, replace=False)
    with pytest.raises(FileExistsError):
        store.save(faq, replace=False)
    with pytest.raises(FileNotFoundError):
        store.save(definition("missing"), replace=True)
    with pytest.raises(FileNotFoundError):
        store.delete("missing")

    path.write_text('{"schema_version": 2, "types": []}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="unsupported"):
        store.get_all()


def test_definition_and_provider_enforce_declarative_contract() -> None:
    with pytest.raises(ValidationError):
        definition("FAQ")
    with pytest.raises(ValidationError):
        definition("faq", display_name="   ")

    item = definition()
    runtime_provider = DeclarativePublicationType(**item.model_dump())
    assert runtime_provider.route_prefix == "/faq"
    assert runtime_provider.render_options().related_enabled is True
    assert runtime_provider.render_options().toc_enabled is False
