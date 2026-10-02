"""Content-addressed local file storage for raw emails, attachments and parser output.

Activities exchange small *references* (paths) through Temporal instead of
large payloads. Swap for S3/Azure Blob/ADLS later behind the same methods.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:120] or "file"


class FileStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save_bytes(self, category: str, name: str, data: bytes) -> Path:
        directory = self.root / category
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _safe(name)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic: retries never see half-written files
        return path

    def save_text(self, category: str, name: str, text: str) -> Path:
        return self.save_bytes(category, name, text.encode("utf-8"))

    def save_json(self, category: str, name: str, payload: Any) -> Path:
        return self.save_text(category, name, json.dumps(payload, indent=2, default=str))

    @staticmethod
    def read_bytes(path: str | Path) -> bytes:
        return Path(path).read_bytes()

    @staticmethod
    def read_text(path: str | Path) -> str:
        return Path(path).read_text(encoding="utf-8")
