"""Email activities: fetch the raw message, parse metadata, extract attachments, acknowledge."""

from __future__ import annotations

import hashlib
import logging

from temporalio import activity

from app.activities.errors import ATTACHMENT_NOT_FOUND, EMAIL_NOT_FOUND, MALFORMED_EMAIL, permanent
from app.email.base import EmailNotFoundError, EmailProvider
from app.email.mime_parser import (
    AttachmentNotFoundError,
    MalformedEmailError,
    extract_attachment,
    extract_metadata,
)
from app.models.schemas import (
    AttachmentRequest,
    DownloadedAttachment,
    EmailMetadata,
    FetchedEmail,
    WorkflowInput,
)
from app.storage.file_store import FileStore, sha256_bytes

logger = logging.getLogger(__name__)


class EmailActivities:
    def __init__(self, provider: EmailProvider, store: FileStore) -> None:
        self.provider = provider
        self.store = store

    @activity.defn(name="fetch_email")
    def fetch_email(self, inp: WorkflowInput) -> FetchedEmail:
        logger.info("Fetching %s message %s", inp.provider, inp.provider_message_id)
        if inp.provider != self.provider.name:
            raise permanent(EMAIL_NOT_FOUND, f"worker serves provider {self.provider.name!r}, not {inp.provider!r}")
        try:
            raw = self.provider.fetch_raw_message(inp.provider_message_id)
        except EmailNotFoundError as exc:
            raise permanent(EMAIL_NOT_FOUND, exc) from exc
        # Store the raw RFC 822 message once; later activities pass only its path around.
        key = hashlib.sha256(f"{inp.provider}:{inp.provider_message_id}".encode()).hexdigest()[:32]
        path = self.store.save_bytes(f"emails/{inp.provider}", f"{key}.eml", raw)
        logger.info("Fetched message %s (%d bytes) -> %s", inp.provider_message_id, len(raw), path)
        return FetchedEmail(
            provider=inp.provider, provider_message_id=inp.provider_message_id, raw_path=str(path), size_bytes=len(raw)
        )

    @activity.defn(name="extract_email_metadata")
    def extract_email_metadata(self, fetched: FetchedEmail) -> EmailMetadata:
        raw = self.store.read_bytes(fetched.raw_path)
        try:
            meta = extract_metadata(
                raw,
                provider=fetched.provider,
                provider_message_id=fetched.provider_message_id,
                thread_id=self.provider.get_thread_id(fetched.provider_message_id),
                raw_path=fetched.raw_path,
            )
        except MalformedEmailError as exc:
            raise permanent(MALFORMED_EMAIL, exc) from exc
        logger.info(
            "Email %s from=%s subject=%r attachments=%s",
            fetched.provider_message_id, meta.sender_email, meta.subject,
            [a.filename for a in meta.attachments],
        )
        return meta

    @activity.defn(name="download_po_attachment")
    def download_po_attachment(self, req: AttachmentRequest) -> DownloadedAttachment:
        raw = self.store.read_bytes(req.raw_path)
        try:
            filename, mime_type, data = extract_attachment(raw, req.attachment_index)
        except AttachmentNotFoundError as exc:
            raise permanent(ATTACHMENT_NOT_FOUND, exc) from exc
        digest = sha256_bytes(data)
        filename = filename or f"attachment-{req.attachment_index}.pdf"
        path = self.store.save_bytes("attachments", f"{digest}.pdf", data)
        logger.info("Saved attachment %s (%d bytes, sha256=%s) -> %s", filename, len(data), digest[:12], path)
        return DownloadedAttachment(
            provider_message_id=req.provider_message_id,
            attachment_index=req.attachment_index,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(data),
            sha256=digest,
            storage_path=str(path),
        )

    @activity.defn(name="acknowledge_email")
    def acknowledge_email(self, inp: WorkflowInput) -> None:
        self.provider.acknowledge(inp.provider_message_id)
        logger.info("Acknowledged message %s", inp.provider_message_id)
