from __future__ import annotations

from argon2 import extract_parameters
from argon2.low_level import Type

from identity.passwords import (
    CHEAPEST_TEST_PASSWORD_HASHER,
    PRODUCTION_PASSWORD_HASHER,
    hash_password,
    verify_password,
)


def test_production_password_policy_is_fixed_argon2id():
    encoded = hash_password("correct horse battery staple")
    parameters = extract_parameters(encoded)
    assert parameters.type is Type.ID
    assert parameters.memory_cost == 65_536
    assert parameters.time_cost == 3
    assert parameters.parallelism == 4
    assert verify_password(encoded, "correct horse battery staple") is True
    assert verify_password(encoded, "wrong") is False


def test_cheapest_hasher_is_explicitly_injectable():
    encoded = hash_password("test-password", hasher=CHEAPEST_TEST_PASSWORD_HASHER)
    parameters = extract_parameters(encoded)
    assert parameters.type is Type.ID
    assert parameters.memory_cost == 8
    assert parameters.time_cost == 1
    assert parameters.parallelism == 1
    assert verify_password(encoded, "test-password", hasher=CHEAPEST_TEST_PASSWORD_HASHER)
    assert PRODUCTION_PASSWORD_HASHER.check_needs_rehash(encoded)


def test_empty_password_and_malformed_hash_are_safe():
    try:
        hash_password("")
    except ValueError as error:
        assert "empty" in str(error)
    else:
        raise AssertionError("empty password was accepted")
    assert verify_password("not-an-argon-hash", "password") is False


def test_unexpected_verifier_failure_is_not_hidden():
    class BrokenHasher:
        def verify(self, *_args):
            raise RuntimeError("unexpected verifier failure")

    try:
        verify_password("hash", "password", hasher=BrokenHasher())
    except RuntimeError as error:
        assert "unexpected verifier failure" in str(error)
    else:
        raise AssertionError("unexpected verifier failure was swallowed")
