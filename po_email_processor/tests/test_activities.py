"""Activity-level tests with Temporal's ActivityEnvironment: outputs and retry classification."""

from __future__ import annotations

import pytest
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from app.activities.document_activities import DocumentActivities
from app.activities.email_activities import EmailActivities
from app.activities.errors import NON_RETRYABLE_ERROR_TYPES
from app.email.local_provider import LocalDirectoryEmailProvider
from app.extraction.pdfplumber_parser import PdfPlumberParser
from app.models.schemas import AttachmentRequest, ParseRequest, WorkflowInput
from app.storage.file_store import FileStore, sha256_bytes


@pytest.fixture
def env():
    return ActivityEnvironment()


@pytest.fixture
def email_acts(tmp_path, sample_dir):
    provider = LocalDirectoryEmailProvider(tmp_path / "inbox", tmp_path / "processed")
    for eml in (sample_dir / "emails").glob("*.eml"):
        provider.deliver(eml)
    return EmailActivities(provider, FileStore(tmp_path / "store"))


@pytest.fixture
def doc_acts(tmp_path):
    return DocumentActivities(PdfPlumberParser(), FileStore(tmp_path / "store"))


def test_email_pipeline_activities(env, email_acts, sample_dir):
    inp = WorkflowInput(provider="local", provider_message_id="po-100245.acme@mail.acme-mfg.example.com")
    fetched = env.run(email_acts.fetch_email, inp)
    meta = env.run(email_acts.extract_email_metadata, fetched)
    assert meta.subject == "Purchase Order PO-100245"
    downloaded = env.run(
        email_acts.download_po_attachment,
        AttachmentRequest(provider_message_id=inp.provider_message_id, raw_path=fetched.raw_path, attachment_index=0),
    )
    pdf = (sample_dir / "pdfs" / "acme_PO-100245.pdf").read_bytes()
    assert downloaded.sha256 == sha256_bytes(pdf)
    assert downloaded.filename == "acme_PO-100245.pdf"

    env.run(email_acts.acknowledge_email, inp)
    assert inp.provider_message_id not in email_acts.provider.list_new_message_ids()


def test_unknown_email_is_non_retryable(env, email_acts):
    with pytest.raises(ApplicationError) as err:
        env.run(email_acts.fetch_email, WorkflowInput(provider="local", provider_message_id="nope"))
    assert err.value.non_retryable and err.value.type == "EmailNotFoundError"


def test_missing_attachment_is_non_retryable(env, email_acts):
    inp = WorkflowInput(provider="local", provider_message_id="no-attachment-question@example.com")
    fetched = env.run(email_acts.fetch_email, inp)
    with pytest.raises(ApplicationError) as err:
        env.run(
            email_acts.download_po_attachment,
            AttachmentRequest(provider_message_id=inp.provider_message_id, raw_path=fetched.raw_path, attachment_index=0),
        )
    assert err.value.non_retryable and err.value.type == "AttachmentNotFoundError"


def test_parse_extract_validate(env, doc_acts, sample_dir):
    ref = env.run(
        doc_acts.parse_document_with_docling,
        ParseRequest(document_id="doc-1", storage_path=str(sample_dir / "pdfs" / "acme_PO-100245.pdf")),
    )
    assert ref.table_count >= 1 and ref.page_count == 1
    extraction = env.run(doc_acts.extract_purchase_order, ref)
    assert extraction.document_id == "doc-1"
    assert len(extraction.purchase_order.line_items) == 5
    validation = env.run(doc_acts.validate_purchase_order, extraction)
    assert validation.is_valid


def test_malformed_pdf_is_non_retryable(env, doc_acts, tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(ApplicationError) as err:
        env.run(doc_acts.parse_document_with_docling, ParseRequest(document_id="d", storage_path=str(bad), filename="PO.pdf"))
    assert err.value.non_retryable and err.value.type == "DocumentParseError"
    assert "PO.pdf" in err.value.message


def test_transient_errors_are_retryable(env, doc_acts, monkeypatch, sample_dir):
    """Unexpected errors (e.g. I/O hiccups) propagate as-is so Temporal retries them."""

    def flaky(_path):
        raise OSError("disk temporarily unavailable")

    monkeypatch.setattr(doc_acts.parser, "parse", flaky)
    with pytest.raises(OSError):
        env.run(
            doc_acts.parse_document_with_docling,
            ParseRequest(document_id="d", storage_path=str(sample_dir / "pdfs" / "acme_PO-100245.pdf")),
        )
    assert "OSError" not in NON_RETRYABLE_ERROR_TYPES
