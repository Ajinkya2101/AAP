"""Local file store (ARCHITECTURE 8.4).

    originals/<sha256[:2]>/<sha256>     uploaded / received files, never changed
    pages/<document_id>/<page_no>.png   page images
    truth/<sha256>.truth.json           synthetic ground truth (invoice generator, stub LLM)
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from aap.config import get_settings


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FileStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or get_settings().filestore_dir)

    # ------------------------------------------------------------ paths
    def original_path(self, sha256: str) -> Path:
        return self.root / "originals" / sha256[:2] / sha256

    def page_path(self, document_id: str, page_no: int) -> Path:
        return self.root / "pages" / str(document_id) / f"{page_no}.png"

    def relative(self, path: Path) -> str:
        return str(path.relative_to(self.root)).replace(os.sep, "/")

    def absolute(self, rel: str) -> Path:
        return self.root / rel

    # ------------------------------------------------------------- write
    def _atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def put_original(self, data: bytes) -> tuple[str, str]:
        """Stores the bytes under their hash. Returns (sha256, relative path)."""
        sha = sha256_bytes(data)
        path = self.original_path(sha)
        if not path.exists():
            self._atomic_write(path, data)
        return sha, self.relative(path)

    def put_page_image(self, document_id: str, page_no: int, png: bytes) -> str:
        path = self.page_path(document_id, page_no)
        self._atomic_write(path, png)
        return self.relative(path)

    def read(self, rel: str) -> bytes:
        return self.absolute(rel).read_bytes()

    def ensure_dirs(self) -> None:
        for d in ("originals", "pages", "truth"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
