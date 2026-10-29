"""File storage for uploaded proof documents (arch §11.4 #22–23).

Files are stored under random keys (``uuid4().hex``); the uploaded file name is only ever kept as data
in the database, never used as a path.  Saving reads the stream in chunks, hashes it on the way and
stops as soon as the size limit is passed, so an oversized upload never lands on disk whole.
"""

import hashlib
import os
import re
import uuid
from pathlib import Path
from typing import BinaryIO, Protocol

from app.core.config import get_settings

CHUNK_SIZE = 64 * 1024
_KEY_PATTERN = re.compile(r"[0-9a-f]{32}")


class FileTooLarge(Exception):
    """The stream passed *max_bytes*; nothing was kept."""


class Storage(Protocol):
    def save(self, stream: BinaryIO, max_bytes: int) -> tuple[str, int, str]:
        """Store *stream*; return ``(key, size, sha256)``.  Raises ``FileTooLarge`` above *max_bytes*."""
        ...

    def open(self, key: str) -> BinaryIO: ...

    def delete(self, key: str) -> None: ...


class LocalStorage:
    """Files on the local disk under *media_root*, one file per key."""

    def __init__(self, media_root: str | os.PathLike[str]) -> None:
        self.root = Path(media_root)

    def _path(self, key: str) -> Path:
        if not _KEY_PATTERN.fullmatch(key):
            raise ValueError("Invalid storage key")
        return self.root / key

    def save(self, stream: BinaryIO, max_bytes: int) -> tuple[str, int, str]:
        self.root.mkdir(parents=True, exist_ok=True)
        key = uuid.uuid4().hex
        path = self._path(key)
        digest = hashlib.sha256()
        size = 0
        try:
            with open(path, "xb") as out:
                while chunk := stream.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLarge()
                    digest.update(chunk)
                    out.write(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return key, size, digest.hexdigest()

    def open(self, key: str) -> BinaryIO:
        return open(self._path(key), "rb")

    def delete(self, key: str) -> None:
        """Remove the file; a missing file is not an error."""
        self._path(key).unlink(missing_ok=True)


def get_storage() -> Storage:
    """The configured storage.  Built per call so tests can point ``media_root`` at a temp dir."""
    return LocalStorage(get_settings().media_root)
