from shared.app_registry import AppRegistry
from shared.internal_links import (
    InternalLinkReference,
    InternalLinkResolverRegistry,
    ProviderLink,
)


class StaticProvider:
    type_id = "note"

    def __init__(self, links: dict[str, ProviderLink]) -> None:
        self.links = links

    def resolve_many(self, targets: tuple[str, ...]) -> dict[str, ProviderLink]:
        return {target: self.links[target] for target in targets if target in self.links}


def registry() -> AppRegistry:
    return AppRegistry.load_default()


def test_registry_deduplicates_and_preserves_first_seen_order() -> None:
    resolver = InternalLinkResolverRegistry(
        {
            "note": StaticProvider(
                {
                    "1": ProviderLink(
                        target="1",
                        href="/apps/notes/?note_id=1",
                        label="문서",
                    )
                }
            )
        },
        registry(),
    )
    results = resolver.resolve_batch(
        (
            InternalLinkReference(type="note", target="1"),
            InternalLinkReference(type="note", target="1"),
            InternalLinkReference(type="unknown", target="2"),
        )
    )
    assert [(result.type, result.target) for result in results] == [
        ("note", "1"),
        ("unknown", "2"),
    ]
    assert results[0].exists is True
    assert results[1].exists is False


def test_registry_rejects_provider_routes_outside_workspace() -> None:
    resolver = InternalLinkResolverRegistry(
        {
            "note": StaticProvider(
                {
                    "1": ProviderLink(
                        target="1",
                        href="javascript:alert(1)",
                        label="위험",
                    ),
                    "2": ProviderLink(
                        target="2",
                        href="/apps/notes/%252e%252e/secret",
                        label="우회",
                    ),
                }
            )
        },
        registry(),
    )
    results = resolver.resolve_batch(
        (
            InternalLinkReference(type="note", target="1"),
            InternalLinkReference(type="note", target="2"),
        )
    )
    assert all(result.exists is False and result.href is None for result in results)


def test_reference_requires_positive_decimal_target() -> None:
    for target in ("0", "-1", "../1", "abc"):
        try:
            InternalLinkReference(type="note", target=target)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted malformed target: {target}")
