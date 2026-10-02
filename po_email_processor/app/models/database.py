"""SQLAlchemy ORM models (PostgreSQL).

    inbound_emails 1 ──< documents 1 ──< purchase_orders 1 ──< purchase_order_items
          │                   │
          └───────────────────┴──< processing_events  (audit trail, polymorphic)

Idempotency keys:
  * inbound_emails  (provider, provider_message_id)  UNIQUE
  * documents       file_hash (SHA-256)              UNIQUE
  * purchase_orders document_id                      UNIQUE
  * purchase_orders dedupe_key (PO no. + supplier)   UNIQUE
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

MONEY = Numeric(18, 4)
QTY = Numeric(18, 4)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class InboundEmail(TimestampMixin, Base):
    __tablename__ = "inbound_emails"
    __table_args__ = (
        UniqueConstraint("provider", "provider_message_id", name="uq_inbound_emails_provider_message"),
        Index("ix_inbound_emails_status", "processing_status"),
        Index("ix_inbound_emails_received_at", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    internet_message_id: Mapped[str | None] = mapped_column(String(998))
    thread_id: Mapped[str | None] = mapped_column(String(998))
    sender_email: Mapped[str | None] = mapped_column(String(320))
    sender_name: Mapped[str | None] = mapped_column(String(320))
    recipient_email: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    cc: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    subject: Mapped[str | None] = mapped_column(Text)
    email_body: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attachments: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")
    raw_storage_path: Mapped[str | None] = mapped_column(Text)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="RECEIVED")
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    workflow_id: Mapped[str | None] = mapped_column(String(512))
    workflow_run_id: Mapped[str | None] = mapped_column(String(128))

    documents: Mapped[list["Document"]] = relationship(back_populates="email")


class Document(TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("file_hash", name="uq_documents_file_hash"),
        Index("ix_documents_email_id", "email_id"),
        Index("ix_documents_status", "processing_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inbound_emails.id", ondelete="CASCADE"), nullable=False
    )
    attachment_index: Mapped[int | None] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_path: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default="PURCHASE_ORDER")
    parser: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    raw_extracted_text: Mapped[str | None] = mapped_column(Text)  # Docling Markdown, kept for audit
    raw_parser_output_path: Mapped[str | None] = mapped_column(Text)  # full Docling JSON on disk
    extraction_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    validation_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    processing_status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="DOCUMENT_DOWNLOADED")
    error_message: Mapped[str | None] = mapped_column(Text)
    error_details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    email: Mapped[InboundEmail] = relationship(back_populates="documents")
    purchase_order: Mapped["PurchaseOrderRecord | None"] = relationship(back_populates="document", uselist=False)


class PurchaseOrderRecord(TimestampMixin, Base):
    __tablename__ = "purchase_orders"
    __table_args__ = (
        UniqueConstraint("document_id", name="uq_purchase_orders_document"),
        UniqueConstraint("dedupe_key", name="uq_purchase_orders_dedupe_key"),
        Index("ix_purchase_orders_po_number", "po_number"),
        Index("ix_purchase_orders_supplier", "supplier_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    email_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inbound_emails.id", ondelete="CASCADE"), nullable=False
    )
    po_number: Mapped[str] = mapped_column(String(128), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(512), nullable=False)
    supplier_name: Mapped[str | None] = mapped_column(Text)
    supplier_email: Mapped[str | None] = mapped_column(String(320))
    supplier_address: Mapped[str | None] = mapped_column(Text)
    buyer_name: Mapped[str | None] = mapped_column(Text)
    buyer_address: Mapped[str | None] = mapped_column(Text)
    order_date: Mapped[date | None] = mapped_column(Date)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(3))
    subtotal: Mapped[Decimal | None] = mapped_column(MONEY)
    tax: Mapped[Decimal | None] = mapped_column(MONEY)
    shipping: Mapped[Decimal | None] = mapped_column(MONEY)
    total_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    payment_terms: Mapped[str | None] = mapped_column(Text)
    shipping_terms: Mapped[str | None] = mapped_column(Text)
    ship_to_address: Mapped[str | None] = mapped_column(Text)
    bill_to_address: Mapped[str | None] = mapped_column(Text)
    validation_warnings: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, server_default="[]")

    document: Mapped[Document] = relationship(back_populates="purchase_order")
    items: Mapped[list["PurchaseOrderItem"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan", order_by="PurchaseOrderItem.sequence"
    )


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    __table_args__ = (
        UniqueConstraint("purchase_order_id", "sequence", name="uq_purchase_order_items_sequence"),
        Index("ix_purchase_order_items_part_number", "part_number"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)  # order on the document
    line_number: Mapped[int | None] = mapped_column(Integer)  # as printed on the document
    part_number: Mapped[str | None] = mapped_column(String(255))
    part_name: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[Decimal | None] = mapped_column(QTY)
    unit_of_measure: Mapped[str | None] = mapped_column(String(32))
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY)
    line_total: Mapped[Decimal | None] = mapped_column(MONEY)
    requested_delivery_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    purchase_order: Mapped[PurchaseOrderRecord] = relationship(back_populates="items")


class ProcessingEvent(Base):
    """Append-only audit log of every status transition, with workflow ids for traceability."""

    __tablename__ = "processing_events"
    __table_args__ = (Index("ix_processing_events_entity", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    entity_type: Mapped[str] = mapped_column(String(16), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    workflow_id: Mapped[str | None] = mapped_column(String(512))
    workflow_run_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
