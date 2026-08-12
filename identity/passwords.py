"""Password hashing policy for identity credentials."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type


# The production policy is part of the authentication contract.  Callers may
# inject a cheaper PasswordHasher in tests, but production code must use this
# instance (or an instance with the same parameters).
PRODUCTION_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65_536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)

# Deliberately exported for tests.  It preserves Argon2id behavior while using
# argon2-cffi's minimum practical work factors.
CHEAPEST_TEST_PASSWORD_HASHER = PasswordHasher(
    time_cost=1,
    memory_cost=8,
    parallelism=1,
    hash_len=4,
    salt_len=8,
    type=Type.ID,
)


def hash_password(
    password: str, *, hasher: PasswordHasher = PRODUCTION_PASSWORD_HASHER
) -> str:
    if not isinstance(password, str) or not password:
        raise ValueError("password must not be empty")
    return hasher.hash(password)


def verify_password(
    password_hash: str,
    password: str,
    *,
    hasher: PasswordHasher = PRODUCTION_PASSWORD_HASHER,
) -> bool:
    if not isinstance(password_hash, str) or not isinstance(password, str):
        return False
    try:
        return hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError):
        # Credential verification is a boolean boundary.  Malformed hashes and
        # mismatched passwords must not expose parser details to callers.
        return False
