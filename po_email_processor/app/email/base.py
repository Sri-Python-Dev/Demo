"""Email provider abstraction.

Every provider exposes messages as raw RFC 822 bytes, so metadata and
attachment parsing is shared (see ``mime_parser``) and the workflow is
identical whether mail comes from a local folder, Gmail, or (later) Microsoft
Graph / a shared mailbox.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmailNotFoundError(Exception):
    """The provider has no message with this id. Permanent."""


class EmailProvider(ABC):
    name: str = "base"

    @abstractmethod
    def list_new_message_ids(self, limit: int = 50) -> list[str]:
        """IDs of messages that have not yet been acknowledged as processed."""

    @abstractmethod
    def fetch_raw_message(self, message_id: str) -> bytes:
        """Raw RFC 822 bytes of a message (including attachments)."""

    def get_thread_id(self, message_id: str) -> str | None:
        """Provider-native thread/conversation id, if the provider has one."""
        return None

    @abstractmethod
    def acknowledge(self, message_id: str) -> None:
        """Mark the message as processed so it is not picked up again (idempotent)."""
