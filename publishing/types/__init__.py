from __future__ import annotations

from publishing.contracts import PublicationType
from publishing.types.manual import ManualPublicationType
from publishing.types.wiki import WikiPublicationType


def default_publication_types() -> tuple[PublicationType, ...]:
    """Return the explicitly composed built-in provider snapshot."""

    return (ManualPublicationType(), WikiPublicationType())


__all__ = [
    "ManualPublicationType",
    "WikiPublicationType",
    "default_publication_types",
]
