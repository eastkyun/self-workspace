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


def test_reference_accepts_positive_decimal_or_canonical_slug_target() -> None:
    assert InternalLinkReference(type="note", target="13").target == "13"
    assert (
        InternalLinkReference(type="manual", target="install-guide").target
        == "install-guide"
    )
    assert InternalLinkReference(type="wiki", target="0").target == "0"
    for target in ("-1", "../1", "Uppercase", "bad_slug", "two--hyphens"):
        try:
            InternalLinkReference(type="note", target=target)
        except ValueError:
            pass
        else:
            raise AssertionError(f"accepted malformed target: {target}")


def test_registry_allows_only_registered_route_boundaries() -> None:
    class ManualRouteRegistry:
        def resolve_route(self, path: str) -> object | None:
            if path == "/manual" or path.startswith("/manual/"):
                return object()
            return None

    resolver = InternalLinkResolverRegistry(
        {
            "note": StaticProvider(
                {
                    "safe": ProviderLink(
                        target="safe",
                        href="/manual/install-guide",
                        label="설치",
                    ),
                    "prefix": ProviderLink(
                        target="prefix",
                        href="/manuals/install-guide",
                        label="유사 경로",
                    ),
                    "traversal": ProviderLink(
                        target="traversal",
                        href="/manual/../secret",
                        label="경로 우회",
                    ),
                    "encoded": ProviderLink(
                        target="encoded",
                        href="/manual/%2e%2e/secret",
                        label="인코딩 우회",
                    ),
                }
            )
        },
        registry(),
        additional_route_registries=(ManualRouteRegistry(),),
    )
    results = resolver.resolve_batch(
        tuple(
            InternalLinkReference(type="note", target=target)
            for target in ("safe", "prefix", "traversal", "encoded")
        )
    )
    by_target = {result.target: result for result in results}
    assert by_target["safe"].exists is True
    assert all(
        by_target[target].exists is False
        for target in ("prefix", "traversal", "encoded")
    )
