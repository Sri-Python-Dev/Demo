"""PostgreSQL persistence and idempotency tests (skipped if PostgreSQL is unavailable)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.extraction.validation import validate_purchase_order
from app.models.database import Document, InboundEmail, ProcessingEvent, PurchaseOrderItem, PurchaseOrderRecord
from app.models.schemas import (
    AttachmentInfo,
    DownloadedAttachment,
    EmailMetadata,
    EntityType,
    ProcessingStatus,
    PurchaseOrder,
    PurchaseOrderLineItem,
    StatusUpdate,
)


def email_meta(message_id: str = "msg-1") -> EmailMetadata:
    return EmailMetadata(
        provider="local",
        provider_message_id=message_id,
        thread_id="t-1",
        sender_email="buyer@acme.example",
        sender_name="Buyer",
        recipient_emails=["po@supplier.example"],
        cc_emails=["cc@acme.example"],
        subject="PO 1",
        received_at=datetime(2026, 3, 1, 12, tzinfo=timezone.utc),
        email_body="see attached",
        attachments=[AttachmentInfo(index=0, filename="po.pdf", mime_type="application/pdf", size_bytes=10)],
    )


def attachment(sha: str = "a" * 64, message_id: str = "msg-1") -> DownloadedAttachment:
    return DownloadedAttachment(
        provider_message_id=message_id, attachment_index=0, filename="po.pdf", mime_type="application/pdf",
        size_bytes=10, sha256=sha, storage_path="/tmp/po.pdf",
    )


def purchase_order(number: str = "PO-1", items: int = 3) -> PurchaseOrder:
    return PurchaseOrder(
        purchase_order_number=number,
        supplier_name="ABC Components",
        order_date=date(2026, 3, 1),
        currency="USD",
        total_amount=Decimal("30.00"),
        line_items=[
            PurchaseOrderLineItem(
                line_number=i, part_number=f"P-{i}", part_name=f"Part {i}", quantity=Decimal("1"),
                unit_of_measure="EA", unit_price=Decimal("10.00"), line_total=Decimal("10.00"),
            )
            for i in range(1, items + 1)
        ],
    )


def count(repo, model) -> int:
    with repo.session_factory() as s:
        return s.scalar(select(func.count()).select_from(model))


def test_full_persistence(repository):
    reg = repository.register_email(email_meta(), "wf-1", "run-1")
    doc = repository.register_document(reg.email_id, attachment())
    validation = validate_purchase_order(purchase_order(items=3))
    saved = repository.save_purchase_order(doc.document_id, validation)

    assert saved.created and saved.line_items_processed == 3
    with repository.session_factory() as s:
        email = s.get(InboundEmail, uuid.UUID(reg.email_id))
        assert email.sender_email == "buyer@acme.example"
        assert email.recipient_email == ["po@supplier.example"]
        assert email.cc == ["cc@acme.example"]
        assert email.workflow_id == "wf-1"
        po = s.get(PurchaseOrderRecord, uuid.UUID(saved.purchase_order_id))
        assert po.po_number == "PO-1" and po.currency == "USD" and po.total_amount == Decimal("30.00")
        assert [i.line_number for i in po.items] == [1, 2, 3]
        assert po.items[0].unit_price == Decimal("10.00")
        assert s.get(Document, uuid.UUID(doc.document_id)).processing_status == "SAVED"
        events = s.scalars(select(ProcessingEvent.status).order_by(ProcessingEvent.id)).all()
        assert events == ["RECEIVED", "DOCUMENT_DOWNLOADED", "SAVED"]


def test_duplicate_email_is_detected_after_completion(repository):
    first = repository.register_email(email_meta())
    doc = repository.register_document(first.email_id, attachment())
    repository.save_purchase_order(doc.document_id, validate_purchase_order(purchase_order()))
    repository.update_status(StatusUpdate(entity_type=EntityType.EMAIL, entity_id=first.email_id, status=ProcessingStatus.SAVED))

    again = repository.register_email(email_meta())
    assert again.already_completed
    assert again.email_id == first.email_id
    assert again.previous_result["documents"][0]["po_number"] == "PO-1"
    assert count(repository, InboundEmail) == 1


def test_failed_email_is_reprocessed_not_duplicated(repository):
    first = repository.register_email(email_meta())
    repository.update_status(StatusUpdate(
        entity_type=EntityType.EMAIL, entity_id=first.email_id, status=ProcessingStatus.FAILED, error_message="boom",
    ))
    again = repository.register_email(email_meta())
    assert not again.already_completed and again.email_id == first.email_id
    with repository.session_factory() as s:
        assert s.get(InboundEmail, uuid.UUID(first.email_id)).error_message is None
    assert count(repository, InboundEmail) == 1


def test_duplicate_attachment_in_another_email(repository):
    e1 = repository.register_email(email_meta("msg-1"))
    d1 = repository.register_document(e1.email_id, attachment())
    saved = repository.save_purchase_order(d1.document_id, validate_purchase_order(purchase_order()))

    e2 = repository.register_email(email_meta("msg-2"))
    d2 = repository.register_document(e2.email_id, attachment(message_id="msg-2"))
    assert d2.is_duplicate
    assert d2.existing_purchase_order_id == saved.purchase_order_id
    assert d2.existing_line_items == 3
    assert count(repository, Document) == 1
    assert count(repository, PurchaseOrderRecord) == 1


def test_save_is_idempotent_for_the_same_document(repository):
    e = repository.register_email(email_meta())
    d = repository.register_document(e.email_id, attachment())
    validation = validate_purchase_order(purchase_order())
    first = repository.save_purchase_order(d.document_id, validation)
    second = repository.save_purchase_order(d.document_id, validation)  # e.g. activity retried after a timeout
    assert first.created and not second.created and not second.is_duplicate
    assert second.purchase_order_id == first.purchase_order_id
    assert count(repository, PurchaseOrderRecord) == 1
    assert count(repository, PurchaseOrderItem) == 3


def test_same_po_number_in_a_different_file_is_a_duplicate(repository):
    e = repository.register_email(email_meta())
    d1 = repository.register_document(e.email_id, attachment("a" * 64))
    d2 = repository.register_document(e.email_id, attachment("b" * 64))  # e.g. re-scanned copy
    first = repository.save_purchase_order(d1.document_id, validate_purchase_order(purchase_order()))
    second = repository.save_purchase_order(d2.document_id, validate_purchase_order(purchase_order()))
    assert second.is_duplicate and second.purchase_order_id == first.purchase_order_id
    with repository.session_factory() as s:
        assert s.get(Document, uuid.UUID(d2.document_id)).processing_status == "DUPLICATE"
    assert count(repository, PurchaseOrderRecord) == 1


def test_failed_document_is_taken_over_by_a_later_email(repository):
    e1 = repository.register_email(email_meta("msg-1"))
    d1 = repository.register_document(e1.email_id, attachment())
    repository.update_status(StatusUpdate(
        entity_type=EntityType.DOCUMENT, entity_id=d1.document_id, status=ProcessingStatus.FAILED, error_message="x",
    ))
    e2 = repository.register_email(email_meta("msg-2"))
    d2 = repository.register_document(e2.email_id, attachment(message_id="msg-2"))
    assert not d2.is_duplicate and d2.document_id == d1.document_id
    with repository.session_factory() as s:
        assert str(s.get(Document, uuid.UUID(d1.document_id)).email_id) == e2.email_id


def test_status_update_records_error_details(repository):
    e = repository.register_email(email_meta())
    repository.update_status(StatusUpdate(
        entity_type=EntityType.EMAIL, entity_id=e.email_id, status=ProcessingStatus.FAILED,
        error_message="parse failed", error_details={"stage": "PARSE_DOCUMENT"}, workflow_id="wf-9",
    ))
    with repository.session_factory() as s:
        email = s.get(InboundEmail, uuid.UUID(e.email_id))
        assert (email.processing_status, email.error_message, email.error_details) == (
            "FAILED", "parse failed", {"stage": "PARSE_DOCUMENT"}
        )
        last = s.scalars(select(ProcessingEvent).order_by(ProcessingEvent.id.desc())).first()
        assert last.workflow_id == "wf-9" and last.status == "FAILED"


def test_cannot_save_without_po_number(repository):
    e = repository.register_email(email_meta())
    d = repository.register_document(e.email_id, attachment())
    with pytest.raises(ValueError):
        repository.save_purchase_order(d.document_id, validate_purchase_order(purchase_order(number=None)))


def test_migrations_match_models(db_engine):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from app.models.database import Base

    with db_engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []
