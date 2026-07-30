"""Workspace-local asset storage and reference management."""

from .database import ASSET_SCHEMA_VERSION, DEFAULT_DB_PATH, initialize_database
from .service import AssetService

__all__ = ["ASSET_SCHEMA_VERSION", "AssetService", "DEFAULT_DB_PATH", "initialize_database"]
