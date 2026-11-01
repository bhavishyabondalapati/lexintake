"""Content-addressed storage for original files.

Each file is stored once, under its own SHA-256: `root/ab/cd/abcd....pdf`. Same bytes, same name,
so storing a duplicate is a no-op and a stored file can never be silently changed. Phase 5's
StorageAdapter files *copies* out of here into folders lawyers browse; this stays the source of
truth for the originals.
"""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol

CHUNK = 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


class BlobStore(Protocol):
    def put(self, src: Path, sha256: str) -> None: ...
    def open(self, sha256: str) -> BinaryIO: ...
    def path(self, sha256: str) -> Path: ...
    def exists(self, sha256: str) -> bool: ...


class LocalBlobStore:
    def __init__(self, root: Path | str, suffix: str = ".pdf") -> None:
        self.root = Path(root)
        self.suffix = suffix

    def path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError(f"not a sha256 hex digest: {sha256!r}")
        return self.root / sha256[:2] / sha256[2:4] / f"{sha256}{self.suffix}"

    def exists(self, sha256: str) -> bool:
        return self.path(sha256).exists()

    def open(self, sha256: str) -> BinaryIO:
        return open(self.path(sha256), "rb")

    def put(self, src: Path, sha256: str) -> None:
        """Copy src in under its hash. Copy to a temp file first, then rename: a crash midway
        never leaves a half-written blob under the real name."""
        dest = self.path(sha256)
        if dest.exists():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
                shutil.copyfileobj(f, out, CHUNK)
            if sha256_file(Path(tmp)) != sha256:
                raise ValueError(f"{src} changed while it was being stored")
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
