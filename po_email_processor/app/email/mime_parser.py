"""Provider-independent parsing of RFC 822 messages: metadata and attachments."""

from __future__ import annotations

import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime

from app.extraction.normalization import clean_text, normalize_email
from app.models.schemas import AttachmentInfo, EmailMetadata


class MalformedEmailError(Exception):
    """The raw message cannot be parsed. Permanent."""


class AttachmentNotFoundError(Exception):
    """The requested attachment is not in the message. Permanent."""


def parse_message(raw: bytes) -> EmailMessage:
    if not raw or not raw.strip():
        raise MalformedEmailError("empty message")
    try:
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    except Exception as exc:  # pragma: no cover - the stdlib parser is very lenient
        raise MalformedEmailError(str(exc)) from exc
    if not msg.keys():
        raise MalformedEmailError("message has no headers")
    return msg  # type: ignore[return-value]


def _attachments(msg: EmailMessage) -> list[EmailMessage]:
    return [part for part in msg.iter_attachments()]


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", text)
    return re.sub(r"<[^>]+>", " ", text)


def _body(msg: EmailMessage) -> str | None:
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return None
    try:
        content = part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    if part.get_content_type() == "text/html":
        content = _html_to_text(content)
    return clean_text(content)


def _addresses(msg: EmailMessage, header: str) -> list[tuple[str, str]]:
    values = msg.get_all(header, [])
    return [(name, addr) for name, addr in getaddresses([str(v) for v in values]) if addr]


def extract_metadata(
    raw: bytes, *, provider: str, provider_message_id: str, thread_id: str | None = None, raw_path: str | None = None
) -> EmailMetadata:
    msg = parse_message(raw)

    senders = _addresses(msg, "From")
    sender_name, sender_email = senders[0] if senders else ("", "")
    received_at = None
    if msg["Date"]:
        try:
            received_at = parsedate_to_datetime(str(msg["Date"]))
        except (TypeError, ValueError):
            received_at = None

    attachments = []
    for index, part in enumerate(_attachments(msg)):
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            AttachmentInfo(
                index=index,
                filename=part.get_filename(),
                mime_type=part.get_content_type(),
                size_bytes=len(payload),
            )
        )

    internet_id = str(msg["Message-ID"]).strip() if msg["Message-ID"] else None
    references = str(msg["References"] or "").split()
    return EmailMetadata(
        provider=provider,
        provider_message_id=provider_message_id,
        internet_message_id=internet_id,
        # Gmail/Graph supply a native thread id; otherwise fall back to the RFC 822 thread root.
        thread_id=thread_id or (references[0] if references else None) or internet_id,
        sender_email=normalize_email(sender_email),
        sender_name=sender_name.strip() or None,
        recipient_emails=[e for _, a in _addresses(msg, "To") if (e := normalize_email(a))],
        cc_emails=[e for _, a in _addresses(msg, "Cc") if (e := normalize_email(a))],
        subject=clean_text(str(msg["Subject"])) if msg["Subject"] else None,
        received_at=received_at,
        email_body=_body(msg),
        attachments=attachments,
        raw_path=raw_path,
    )


def extract_attachment(raw: bytes, index: int) -> tuple[str | None, str, bytes]:
    """Return (filename, mime_type, bytes) for the attachment at ``index``."""
    msg = parse_message(raw)
    parts = _attachments(msg)
    if index < 0 or index >= len(parts):
        raise AttachmentNotFoundError(f"attachment index {index} not found (message has {len(parts)})")
    part = parts[index]
    payload = part.get_payload(decode=True)
    if payload is None:
        raise AttachmentNotFoundError(f"attachment {index} has no payload")
    return part.get_filename(), part.get_content_type(), payload


def message_id_from_raw(raw: bytes) -> str | None:
    """The RFC 822 Message-ID without angle brackets (used as the local provider's id)."""
    msg = parse_message(raw)
    value = msg["Message-ID"]
    return str(value).strip().strip("<>") if value else None
