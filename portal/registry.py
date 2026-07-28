from shared.app_registry import (
    PORTAL_DIR,
    PORTAL_STATIC_DIR,
    REGISTRY_PATH,
    HubApp,
    load_apps,
    navigation_apps,
)

STATIC_DIR = PORTAL_STATIC_DIR

__all__ = [
    "HubApp",
    "PORTAL_DIR",
    "REGISTRY_PATH",
    "STATIC_DIR",
    "load_apps",
    "navigation_apps",
]
