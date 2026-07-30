from __future__ import annotations

from pathlib import Path

import pytest

from assets.storage import LocalFileStorage, UnsafeStorageKeyError, content_storage_key, digest_bytes


def test_local_storage_uses_content_addressed_paths(tmp_path: Path) -> None:
    content = b"normalized-image"
    digest = digest_bytes(content)
    key = content_storage_key(digest, "png")
    storage = LocalFileStorage(tmp_path / "blobs")

    storage.put(key, content)
    storage.put(key, content)

    assert key == f"{digest[:2]}/{digest[2:4]}/{digest}.png"
    assert storage.exists(key)
    assert storage.open(key) == content


def test_storage_rejects_traversal_and_content_collision(tmp_path: Path) -> None:
    storage = LocalFileStorage(tmp_path / "blobs")
    for key in ("../secret", "/tmp/secret", "aa/bb/not-a-hash.png", "aa\\bb\\evil.png"):
        with pytest.raises(UnsafeStorageKeyError):
            storage.open(key)

    digest = digest_bytes(b"one")
    key = content_storage_key(digest, "jpg")
    storage.put(key, b"one")
    with pytest.raises(RuntimeError, match="does not match"):
        storage.put(key, b"two")
