"""Local folder email provider for demos and tests.

Drop ``.eml`` files into ``LOCAL_INBOX_DIR``; each file is a message whose id is
its RFC 822 ``Message-ID`` (without angle brackets). Acknowledged messages are
moved to ``LOCAL_PROCESSED_DIR``. It exercises exactly the same workflow as
the Gmail provider.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from app.email.base import EmailNotFoundError, EmailProvider
from app.email.mime_parser import MalformedEmailError, message_id_from_raw

logger = logging.getLogger(__name__)


class LocalDirectoryEmailProvider(EmailProvider):
    name = "local"

    def __init__(self, inbox_dir: Path, processed_dir: Path) -> None:
        self.inbox_dir = Path(inbox_dir)
        self.processed_dir = Path(processed_dir)
        self.inbox_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def message_id_for(path: Path) -> str:
        raw = path.read_bytes()
        try:
            message_id = message_id_from_raw(raw)
        except MalformedEmailError:
            message_id = None
        # Messages without a Message-ID get a stable content-hash id.
        return message_id or f"sha256-{hashlib.sha256(raw).hexdigest()[:32]}"

    def _index(self, *dirs: Path) -> dict[str, Path]:
        index: dict[str, Path] = {}
        for directory in dirs:
            for path in sorted(directory.glob("*.eml")):
                index.setdefault(self.message_id_for(path), path)
        return index

    def list_new_message_ids(self, limit: int = 50) -> list[str]:
        return list(self._index(self.inbox_dir))[:limit]

    def fetch_raw_message(self, message_id: str) -> bytes:
        path = self._index(self.inbox_dir, self.processed_dir).get(message_id)
        if path is None:
            raise EmailNotFoundError(f"message {message_id!r} not found in {self.inbox_dir}")
        return path.read_bytes()

    def acknowledge(self, message_id: str) -> None:
        path = self._index(self.inbox_dir).get(message_id)
        if path is None:
            return  # already acknowledged
        target = self.processed_dir / path.name
        shutil.move(str(path), target)
        logger.info("Moved %s to %s", path.name, self.processed_dir)

    def deliver(self, eml_path: Path) -> str:
        """Demo helper: 'send' an email by copying it into the inbox. Returns its message id."""
        target = self.inbox_dir / Path(eml_path).name
        shutil.copyfile(eml_path, target)
        return self.message_id_for(target)
