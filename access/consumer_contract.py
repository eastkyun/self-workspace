"""Architecture-level declaration of shared authorization consumers."""

AUTHORIZATION_CONSUMER_KEYS = frozenset(
    {
        "notes",
        "calendar",
        "publishing",
        "assets",
        "search",
        "dashboard",
        "internal-links",
        "ai-extension",
    }
)
