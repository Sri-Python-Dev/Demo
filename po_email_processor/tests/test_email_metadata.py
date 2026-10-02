from datetime import datetime, timezone

import pytest

from app.email.base import EmailNotFoundError
from app.email.local_provider import LocalDirectoryEmailProvider
from app.email.mime_parser import (
    AttachmentNotFoundError,
    MalformedEmailError,
    extract_attachment,
    extract_metadata,
)


def test_extracts_full_metadata(sample_dir):
    raw = (sample_dir / "emails" / "01_acme_po.eml").read_bytes()
    meta = extract_metadata(raw, provider="local", provider_message_id="m1")

    assert meta.provider_message_id == "m1"
    assert meta.internet_message_id == "<po-100245.acme@mail.acme-mfg.example.com>"
    assert meta.thread_id == "<po-100245.acme@mail.acme-mfg.example.com>"
    assert meta.sender_email == "jane.buyer@acme-mfg.example.com"
    assert meta.sender_name == "Jane Buyer"
    assert meta.recipient_emails == ["po-inbox@abccomponents.example.com"]
    assert meta.cc_emails == ["procurement@acme-mfg.example.com", "ap@acme-mfg.example.com"]
    assert meta.subject == "Purchase Order PO-100245"
    assert meta.received_at == datetime(2026, 3, 15, 14, 32, 10, tzinfo=timezone.utc)
    assert "Please find attached Purchase Order PO-100245" in meta.email_body
    assert len(meta.attachments) == 1
    att = meta.attachments[0]
    assert (att.filename, att.mime_type, att.index) == ("acme_PO-100245.pdf", "application/pdf", 0)
    assert att.size_bytes and att.size_bytes > 1000
    assert meta.pdf_attachments() == [att]


def test_native_thread_id_wins(sample_dir):
    raw = (sample_dir / "emails" / "01_acme_po.eml").read_bytes()
    meta = extract_metadata(raw, provider="gmail", provider_message_id="abc", thread_id="thread-42")
    assert meta.thread_id == "thread-42"


def test_email_without_attachment(sample_dir):
    raw = (sample_dir / "emails" / "06_no_attachment.eml").read_bytes()
    meta = extract_metadata(raw, provider="local", provider_message_id="x")
    assert meta.attachments == []
    assert meta.pdf_attachments() == []
    assert meta.sender_name is None


def test_attachment_bytes_round_trip(sample_dir):
    raw = (sample_dir / "emails" / "01_acme_po.eml").read_bytes()
    filename, mime, data = extract_attachment(raw, 0)
    assert filename == "acme_PO-100245.pdf"
    assert mime == "application/pdf"
    assert data == (sample_dir / "pdfs" / "acme_PO-100245.pdf").read_bytes()
    with pytest.raises(AttachmentNotFoundError):
        extract_attachment(raw, 3)


@pytest.mark.parametrize("raw", [b"", b"   \n", b"just some text without headers"])
def test_malformed_email(raw):
    with pytest.raises(MalformedEmailError):
        extract_metadata(raw, provider="local", provider_message_id="bad")


def test_local_provider_lifecycle(tmp_path, sample_dir):
    provider = LocalDirectoryEmailProvider(tmp_path / "inbox", tmp_path / "done")
    message_id = provider.deliver(sample_dir / "emails" / "01_acme_po.eml")
    assert message_id == "po-100245.acme@mail.acme-mfg.example.com"
    assert provider.list_new_message_ids() == [message_id]
    assert b"Message-ID: <po-100245.acme@" in provider.fetch_raw_message(message_id)

    provider.acknowledge(message_id)
    provider.acknowledge(message_id)  # idempotent
    assert provider.list_new_message_ids() == []
    assert provider.fetch_raw_message(message_id)  # still fetchable after acknowledgement (retries/replays)

    with pytest.raises(EmailNotFoundError):
        provider.fetch_raw_message("does-not-exist")
