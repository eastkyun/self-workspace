"""Versioned, single-database SQLite migration primitives."""

from .contract import Migration, MigrationConnection, MigrationContext, MigrationStep
from .manifest import (
    MANIFEST_VERSION,
    ManifestError,
    advance_artifact,
    read_manifest,
    validate_manifest,
    write_manifest,
)
from .runner import MigrationError, MigrationResult, migrate
from .verification import (
    DatabaseBoundaryError,
    DatabaseVerificationError,
    assert_single_database,
    get_user_version,
    verify_database,
)

__all__ = [
    "DatabaseBoundaryError",
    "DatabaseVerificationError",
    "Migration",
    "MANIFEST_VERSION",
    "ManifestError",
    "MigrationContext",
    "MigrationConnection",
    "MigrationError",
    "MigrationResult",
    "MigrationStep",
    "assert_single_database",
    "advance_artifact",
    "get_user_version",
    "migrate",
    "read_manifest",
    "validate_manifest",
    "verify_database",
    "write_manifest",
]
