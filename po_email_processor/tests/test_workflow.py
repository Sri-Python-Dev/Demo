"""Temporal workflow tests against a real (embedded) Temporal dev server.

* Mocked activities verify orchestration: retries of transient failures,
  fail-fast on permanent errors, validation errors never retried, duplicate and
  ignored emails.
* An end-to-end test runs the real activities (local email folder, pdfplumber
  parser, PostgreSQL) through the real workflow.
"""

from __future__ import annotations

import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError
from temporalio.worker import Worker

from app.models.schemas import (
    AttachmentInfo,
    AttachmentRequest,
    DocumentValidationRequest,
    DownloadedAttachment,
    EmailMetadata,
    ExtractionResult,
    FetchedEmail,
    ParsedDocumentRef,
    ParseRequest,
    PurchaseOrder,
    PurchaseOrderLineItem,
    RegisteredDocument,
    RegisteredEmail,
    RegisterDocumentRequest,
    SavedPurchaseOrder,
    StatusUpdate,
    ValidationIssue,
    ValidationResult,
    WorkflowInput,
    WorkflowResult,
)
from app.workflows.purchase_order_workflow import PurchaseOrderEmailWorkflow


class MockActivities:
    """Activity doubles registered under the real activity names."""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()
        self.statuses: list[tuple[str, str]] = []
        self.attachments = [AttachmentInfo(index=0, filename="po.pdf", mime_type="application/pdf", size_bytes=10)]
        self.already_completed = False
        self.register_document_failures = 0
        self.parse_error: Exception | None = None
        self.valid = True

    def activities(self) -> list:
        return [
            self.fetch_email, self.extract_email_metadata, self.download_po_attachment, self.acknowledge_email,
            self.parse_document_with_docling, self.extract_purchase_order, self.validate_purchase_order,
            self.register_email, self.register_document, self.store_parsed_document, self.store_extraction_result,
            self.store_validation_result, self.save_purchase_order_to_postgres, self.update_processing_status,
        ]

    def _po(self) -> PurchaseOrder:
        return PurchaseOrder(
            purchase_order_number="PO-1",
            line_items=[PurchaseOrderLineItem(line_number=i, part_number=f"P{i}", quantity=Decimal(1)) for i in (1, 2)],
        )

    @activity.defn(name="fetch_email")
    async def fetch_email(self, inp: WorkflowInput) -> FetchedEmail:
        self.calls["fetch_email"] += 1
        return FetchedEmail(provider=inp.provider, provider_message_id=inp.provider_message_id, raw_path="/x.eml", size_bytes=1)

    @activity.defn(name="extract_email_metadata")
    async def extract_email_metadata(self, fetched: FetchedEmail) -> EmailMetadata:
        self.calls["extract_email_metadata"] += 1
        return EmailMetadata(provider="local", provider_message_id=fetched.provider_message_id, attachments=self.attachments)

    @activity.defn(name="register_email")
    async def register_email(self, meta: EmailMetadata) -> RegisteredEmail:
        self.calls["register_email"] += 1
        if self.already_completed:
            return RegisteredEmail(
                email_id="e-1", status="SAVED", already_completed=True,
                previous_result={"documents": [{"filename": "po.pdf", "document_id": "d-1", "purchase_order_id": "p-1",
                                                "po_number": "PO-1", "line_items_processed": 2}]},
            )
        return RegisteredEmail(email_id="e-1", status="RECEIVED")

    @activity.defn(name="download_po_attachment")
    async def download_po_attachment(self, req: AttachmentRequest) -> DownloadedAttachment:
        self.calls["download_po_attachment"] += 1
        return DownloadedAttachment(
            provider_message_id=req.provider_message_id, attachment_index=0, filename="po.pdf",
            mime_type="application/pdf", size_bytes=10, sha256="0" * 64, storage_path="/x.pdf",
        )

    @activity.defn(name="register_document")
    async def register_document(self, req: RegisterDocumentRequest) -> RegisteredDocument:
        self.calls["register_document"] += 1
        if self.calls["register_document"] <= self.register_document_failures:
            raise RuntimeError("database connection reset")  # transient -> retried
        return RegisteredDocument(document_id="d-1")

    @activity.defn(name="parse_document_with_docling")
    async def parse_document_with_docling(self, req: ParseRequest) -> ParsedDocumentRef:
        self.calls["parse_document_with_docling"] += 1
        if self.parse_error:
            raise self.parse_error
        return ParsedDocumentRef(document_id=req.document_id, parser="mock", parsed_path="/p", markdown_path="/m")

    @activity.defn(name="store_parsed_document")
    async def store_parsed_document(self, ref: ParsedDocumentRef) -> None:
        self.calls["store_parsed_document"] += 1

    @activity.defn(name="extract_purchase_order")
    async def extract_purchase_order(self, ref: ParsedDocumentRef) -> ExtractionResult:
        self.calls["extract_purchase_order"] += 1
        return ExtractionResult(document_id=ref.document_id, extractor="mock", extractor_version="1", purchase_order=self._po())

    @activity.defn(name="store_extraction_result")
    async def store_extraction_result(self, extraction: ExtractionResult) -> None:
        self.calls["store_extraction_result"] += 1

    @activity.defn(name="validate_purchase_order")
    async def validate_purchase_order(self, extraction: ExtractionResult) -> ValidationResult:
        self.calls["validate_purchase_order"] += 1
        errors = [] if self.valid else [ValidationIssue(field="purchase_order_number", message="required", severity="error")]
        return ValidationResult(is_valid=self.valid, errors=errors, purchase_order=extraction.purchase_order)

    @activity.defn(name="store_validation_result")
    async def store_validation_result(self, req: DocumentValidationRequest) -> None:
        self.calls["store_validation_result"] += 1

    @activity.defn(name="save_purchase_order_to_postgres")
    async def save_purchase_order_to_postgres(self, req: DocumentValidationRequest) -> SavedPurchaseOrder:
        self.calls["save_purchase_order_to_postgres"] += 1
        return SavedPurchaseOrder(purchase_order_id="p-1", po_number="PO-1", line_items_processed=2)

    @activity.defn(name="update_processing_status")
    async def update_processing_status(self, update: StatusUpdate) -> None:
        self.calls["update_processing_status"] += 1
        self.statuses.append((update.entity_type.value, update.status.value))

    @activity.defn(name="acknowledge_email")
    async def acknowledge_email(self, inp: WorkflowInput) -> None:
        self.calls["acknowledge_email"] += 1


async def run_with(env, mocks: MockActivities) -> WorkflowResult:
    queue = f"test-{uuid.uuid4()}"
    async with Worker(env.client, task_queue=queue, workflows=[PurchaseOrderEmailWorkflow], activities=mocks.activities()):
        return await env.client.execute_workflow(
            PurchaseOrderEmailWorkflow.run,
            WorkflowInput(provider="local", provider_message_id="m-1"),
            id=f"wf-{uuid.uuid4()}",
            task_queue=queue,
        )


async def test_happy_path(temporal_env):
    mocks = MockActivities()
    result = await run_with(temporal_env, mocks)
    assert result.status == "COMPLETED"
    assert (result.email_id, result.document_id, result.purchase_order_id, result.po_number) == ("e-1", "d-1", "p-1", "PO-1")
    assert result.line_items_processed == 2
    assert mocks.statuses == [("EMAIL", "EMAIL_PARSED"), ("EMAIL", "SAVED")]
    assert mocks.calls["acknowledge_email"] == 1


async def test_transient_activity_failure_is_retried(temporal_env):
    mocks = MockActivities()
    mocks.register_document_failures = 2
    result = await run_with(temporal_env, mocks)
    assert result.status == "COMPLETED"
    assert mocks.calls["register_document"] == 3  # 2 failures + 1 success


async def test_permanent_parse_error_fails_fast(temporal_env):
    mocks = MockActivities()
    mocks.parse_error = ApplicationError("not a PDF", type="DocumentParseError", non_retryable=True)
    with pytest.raises(WorkflowFailureError) as err:
        await run_with(temporal_env, mocks)
    assert "DocumentParseError" in str(err.value.cause)
    assert mocks.calls["parse_document_with_docling"] == 1  # not retried
    assert mocks.calls["extract_purchase_order"] == 0
    assert ("DOCUMENT", "FAILED") in mocks.statuses and ("EMAIL", "FAILED") in mocks.statuses
    assert mocks.calls["acknowledge_email"] == 1  # terminal: not re-polled; failure kept in DB


async def test_retryable_error_type_listed_as_non_retryable_is_not_retried(temporal_env):
    """Even a plain (retryable) ApplicationError whose *type* is permanent is not retried."""
    mocks = MockActivities()
    mocks.parse_error = ApplicationError("corrupt", type="DocumentParseError")
    with pytest.raises(WorkflowFailureError):
        await run_with(temporal_env, mocks)
    assert mocks.calls["parse_document_with_docling"] == 1


async def test_validation_errors_are_not_retried_and_not_saved(temporal_env):
    mocks = MockActivities()
    mocks.valid = False
    with pytest.raises(WorkflowFailureError) as err:
        await run_with(temporal_env, mocks)
    assert "PurchaseOrderValidationError" in str(err.value.cause)
    assert mocks.calls["validate_purchase_order"] == 1
    assert mocks.calls["save_purchase_order_to_postgres"] == 0


async def test_already_processed_email_returns_duplicate(temporal_env):
    mocks = MockActivities()
    mocks.already_completed = True
    result = await run_with(temporal_env, mocks)
    assert result.status == "DUPLICATE"
    assert result.purchase_order_id == "p-1" and result.line_items_processed == 2
    assert mocks.calls["download_po_attachment"] == 0


async def test_email_without_pdf_is_ignored(temporal_env):
    mocks = MockActivities()
    mocks.attachments = []
    result = await run_with(temporal_env, mocks)
    assert result.status == "IGNORED"
    assert mocks.statuses == [("EMAIL", "IGNORED")]


# --------------------------------------------------------------------------
# End to end: real activities + PostgreSQL + local email folder
# --------------------------------------------------------------------------
async def test_end_to_end_with_real_activities(temporal_env, repository, tmp_path, sample_dir):
    from sqlalchemy import func, select

    from app.activities.database_activities import DatabaseActivities
    from app.activities.document_activities import DocumentActivities
    from app.activities.email_activities import EmailActivities
    from app.email.local_provider import LocalDirectoryEmailProvider
    from app.extraction.pdfplumber_parser import PdfPlumberParser
    from app.models.database import InboundEmail, PurchaseOrderItem, PurchaseOrderRecord
    from app.storage.file_store import FileStore

    provider = LocalDirectoryEmailProvider(tmp_path / "inbox", tmp_path / "processed")
    store = FileStore(tmp_path / "storage")
    email, docs, db = EmailActivities(provider, store), DocumentActivities(PdfPlumberParser(), store), DatabaseActivities(repository)
    activities = [
        email.fetch_email, email.extract_email_metadata, email.download_po_attachment, email.acknowledge_email,
        docs.parse_document_with_docling, docs.extract_purchase_order, docs.validate_purchase_order,
        db.register_email, db.register_document, db.store_parsed_document, db.store_extraction_result,
        db.store_validation_result, db.save_purchase_order_to_postgres, db.update_processing_status,
    ]
    queue = f"e2e-{uuid.uuid4()}"

    async def process(eml: str, wf_id: str | None = None):
        message_id = provider.deliver(sample_dir / "emails" / eml)
        return await temporal_env.client.execute_workflow(
            PurchaseOrderEmailWorkflow.run,
            WorkflowInput(provider="local", provider_message_id=message_id),
            id=wf_id or f"e2e-{uuid.uuid4()}",
            task_queue=queue,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        async with Worker(
            temporal_env.client, task_queue=queue, workflows=[PurchaseOrderEmailWorkflow],
            activities=activities, activity_executor=executor,
        ):
            first = await process("01_acme_po.eml")
            assert first.status == "COMPLETED"
            assert first.po_number == "PO-100245" and first.line_items_processed == 5

            # Same email delivered again (new workflow run): DB idempotency returns the original PO.
            again = await process("01_acme_po.eml")
            assert again.status == "DUPLICATE" and again.purchase_order_id == first.purchase_order_id

            # Different email, same PDF attachment.
            resent = await process("04_acme_po_resent.eml")
            assert resent.status == "DUPLICATE" and resent.purchase_order_id == first.purchase_order_id

            multi = await process("02_globex_po_multipage.eml")
            assert multi.status == "COMPLETED" and multi.line_items_processed == 28

            ignored = await process("06_no_attachment.eml")
            assert ignored.status == "IGNORED"

            with pytest.raises(WorkflowFailureError):
                await process("05_malformed_pdf.eml")

    with repository.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(PurchaseOrderRecord)) == 2
        assert s.scalar(select(func.count()).select_from(PurchaseOrderItem)) == 33
        statuses = dict(s.execute(select(InboundEmail.provider_message_id, InboundEmail.processing_status)).all())
    assert statuses == {
        "po-100245.acme@mail.acme-mfg.example.com": "SAVED",
        "po-100245.resend@mail.acme-mfg.example.com": "DUPLICATE",
        "gx-po-2026-0042@globex.example.de": "SAVED",
        "no-attachment-question@example.com": "IGNORED",
        "po-999@broken-supplier.example.com": "FAILED",
    }
    assert provider.list_new_message_ids() == []  # everything acknowledged
