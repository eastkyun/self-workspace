from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol

_STORAGE_KEY = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{2}/[0-9a-f]{64}\.(png|jpg|webp)$")


class StorageProvider(Protocol):
    def put(self, storage_key: str, content: bytes) -> None: ...
    def open(self, storage_key: str) -> bytes: ...
    def exists(self, storage_key: str) -> bool: ...
    def delete(self, storage_key: str) -> None: ...


class UnsafeStorageKeyError(ValueError):
    pass


class LocalFileStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def _path(self, storage_key: str) -> Path:
        if not _STORAGE_KEY.fullmatch(storage_key):
            raise UnsafeStorageKeyError("invalid content-addressed storage key")
        path = (self.root / storage_key).resolve()
        if self.root not in path.parents:
            raise UnsafeStorageKeyError("storage key escapes storage root")
        return path

    def put(self, storage_key: str, content: bytes) -> None:
        path = self._path(storage_key)
        if path.exists():
            if path.read_bytes() != content:
                raise RuntimeError("content-addressed asset does not match existing blob")
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".asset-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as temporary:
                temporary.write(content)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

    def open(self, storage_key: str) -> bytes:
        return self._path(storage_key).read_bytes()

    def exists(self, storage_key: str) -> bool:
        return self._path(storage_key).is_file()

    def delete(self, storage_key: str) -> None:
        self._path(storage_key).unlink(missing_ok=True)


def content_storage_key(sha256: str, extension: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("invalid SHA-256 digest")
    if extension not in {"png", "jpg", "webp"}:
        raise ValueError("unsupported asset extension")
    return f"{sha256[:2]}/{sha256[2:4]}/{sha256}.{extension}"


def digest_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()
