"""Pydantic schemas shared by activities, the workflow and the extraction layer.

These are the contracts that cross the Temporal boundary (activity inputs and
outputs), so they must stay JSON serialisable and free of I/O.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------
class ProcessingStatus(str, Enum):
    RECEIVED = "RECEIVED"
    EMAIL_PARSED = "EMAIL_PARSED"
    DOCUMENT_DOWNLOADED = "DOCUMENT_DOWNLOADED"
    DOCUMENT_PARSED = "DOCUMENT_PARSED"
    EXTRACTED = "EXTRACTED"
    VALIDATED = "VALIDATED"
    SAVED = "SAVED"
    DUPLICATE = "DUPLICATE"
    IGNORED = "IGNORED"  # e.g. an email with no PDF attachment
    FAILED = "FAILED"


class DocumentType(str, Enum):
    PURCHASE_ORDER = "PURCHASE_ORDER"
    # Reserved for future document families; the pipeline already carries the type.
    SUPPLIER_QUOTE = "SUPPLIER_QUOTE"
    RFQ = "RFQ"
    ASN = "ASN"
    UNKNOWN = "UNKNOWN"


class EntityType(str, Enum):
    EMAIL = "EMAIL"
    DOCUMENT = "DOCUMENT"


class _Base(BaseModel):
    model_config = ConfigDict(use_enum_values=False, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
class AttachmentInfo(_Base):
    index: int = Field(description="Position of the attachment among the message's attachments.")
    filename: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None

    @property
    def is_pdf(self) -> bool:
        name = (self.filename or "").lower()
        return (self.mime_type or "").lower() == "application/pdf" or name.endswith(".pdf")


class FetchedEmail(_Base):
    """Reference to a raw RFC 822 message persisted in local storage."""

    provider: str
    provider_message_id: str
    raw_path: str
    size_bytes: int


class EmailMetadata(_Base):
    provider: str
    provider_message_id: str
    internet_message_id: str | None = None
    thread_id: str | None = None
    sender_email: str | None = None
    sender_name: str | None = None
    recipient_emails: list[str] = Field(default_factory=list)
    cc_emails: list[str] = Field(default_factory=list)
    subject: str | None = None
    received_at: datetime | None = None
    email_body: str | None = None
    attachments: list[AttachmentInfo] = Field(default_factory=list)
    raw_path: str | None = None

    def pdf_attachments(self) -> list[AttachmentInfo]:
        return [a for a in self.attachments if a.is_pdf]


class RegisteredEmail(_Base):
    email_id: str
    status: ProcessingStatus
    already_completed: bool = False
    # When the email was already processed, the previous outcome is echoed back.
    previous_result: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
class DownloadedAttachment(_Base):
    provider_message_id: str
    attachment_index: int
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_path: str


class DocumentMetadata(_Base):
    document_id: str
    email_id: str
    filename: str
    mime_type: str
    file_hash: str
    size_bytes: int
    storage_path: str
    document_type: DocumentType = DocumentType.PURCHASE_ORDER


class RegisteredDocument(_Base):
    document_id: str
    is_duplicate: bool = False
    duplicate_of_document_id: str | None = None
    existing_purchase_order_id: str | None = None
    existing_po_number: str | None = None
    existing_line_items: int | None = None


class ParsedTable(_Base):
    """A table as a grid of cell strings. Row 0 is usually (not always) a header."""

    page_no: int | None = None
    rows: list[list[str]]


class ParsedDocument(_Base):
    """Parser-agnostic representation produced by Docling (or a fallback parser)."""

    document_id: str | None = None
    parser: str
    page_count: int | None = None
    markdown: str
    text_blocks: list[str] = Field(default_factory=list)
    tables: list[ParsedTable] = Field(default_factory=list)


class ParsedDocumentRef(_Base):
    """Small handle returned by the parse activity; full content lives on disk."""

    document_id: str
    parser: str
    parsed_path: str
    markdown_path: str
    raw_output_path: str | None = None
    page_count: int | None = None
    table_count: int = 0
    text_length: int = 0


# ---------------------------------------------------------------------------
# Purchase order
# ---------------------------------------------------------------------------
class PurchaseOrderLineItem(_Base):
    line_number: int | None = None
    part_number: str | None = None
    part_name: str | None = None
    quantity: Decimal | None = None
    unit_of_measure: str | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    requested_delivery_date: date | None = None


class PurchaseOrder(_Base):
    purchase_order_number: str | None = None
    supplier_name: str | None = None
    supplier_email: str | None = None
    supplier_address: str | None = None
    buyer_name: str | None = None
    buyer_address: str | None = None
    order_date: date | None = None
    requested_delivery_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    shipping: Decimal | None = None
    total_amount: Decimal | None = None
    payment_terms: str | None = None
    shipping_terms: str | None = None
    ship_to_address: str | None = None
    bill_to_address: str | None = None
    line_items: list[PurchaseOrderLineItem] = Field(default_factory=list)


class ExtractionResult(_Base):
    document_id: str | None = None
    document_type: DocumentType = DocumentType.PURCHASE_ORDER
    extractor: str
    extractor_version: str
    purchase_order: PurchaseOrder
    warnings: list[str] = Field(default_factory=list)
    # Placeholders for future field-level confidence / evidence.
    field_confidence: dict[str, float] = Field(default_factory=dict)
    field_evidence: dict[str, str] = Field(default_factory=dict)


class ValidationIssue(_Base):
    field: str
    message: str
    severity: str  # "error" | "warning"


class ValidationResult(_Base):
    is_valid: bool
    errors: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
    purchase_order: PurchaseOrder


class SavedPurchaseOrder(_Base):
    purchase_order_id: str
    po_number: str | None
    line_items_processed: int
    created: bool = True
    # True when a *different* document already produced this PO (same PO number/supplier/buyer).
    is_duplicate: bool = False
    duplicate_reason: str | None = None


# ---------------------------------------------------------------------------
# Workflow I/O
# ---------------------------------------------------------------------------
class WorkflowInput(_Base):
    provider: str
    provider_message_id: str


class DocumentOutcome(_Base):
    filename: str
    status: ProcessingStatus
    document_id: str | None = None
    purchase_order_id: str | None = None
    po_number: str | None = None
    line_items_processed: int = 0
    error: str | None = None


class WorkflowResult(_Base):
    status: str  # COMPLETED | DUPLICATE | IGNORED
    email_id: str | None = None
    document_id: str | None = None
    purchase_order_id: str | None = None
    po_number: str | None = None
    line_items_processed: int = 0
    documents: list[DocumentOutcome] = Field(default_factory=list)


class StatusUpdate(_Base):
    entity_type: EntityType
    entity_id: str
    status: ProcessingStatus
    error_message: str | None = None
    error_details: dict[str, Any] | None = None
    workflow_id: str | None = None
    run_id: str | None = None


# ---------------------------------------------------------------------------
# Activity request envelopes (single-argument activities evolve more safely)
# ---------------------------------------------------------------------------
class AttachmentRequest(_Base):
    provider_message_id: str
    raw_path: str
    attachment_index: int


class ParseRequest(_Base):
    document_id: str
    storage_path: str
    filename: str | None = None


class RegisterDocumentRequest(_Base):
    email_id: str
    attachment: DownloadedAttachment
    document_type: DocumentType = DocumentType.PURCHASE_ORDER


class DocumentValidationRequest(_Base):
    document_id: str
    validation: ValidationResult
