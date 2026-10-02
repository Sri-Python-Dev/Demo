"""PostgreSQL repository: every write is idempotent so Temporal can retry safely."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.connection import make_session_factory, session_scope
from app.models.database import (
    Document,
    InboundEmail,
    ProcessingEvent,
    PurchaseOrderItem,
    PurchaseOrderRecord,
)
from app.models.schemas import (
    DocumentType,
    DownloadedAttachment,
    EmailMetadata,
    EntityType,
    ExtractionResult,
    ProcessingStatus,
    PurchaseOrder,
    RegisteredDocument,
    RegisteredEmail,
    SavedPurchaseOrder,
    StatusUpdate,
    ValidationResult,
)

logger = logging.getLogger(__name__)

TERMINAL_EMAIL_STATUSES = {ProcessingStatus.SAVED.value, ProcessingStatus.DUPLICATE.value, ProcessingStatus.IGNORED.value}


def _norm_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


def purchase_order_dedupe_key(po: PurchaseOrder) -> str:
    """Business identity of a PO: PO number + supplier + buyer (normalised)."""
    return "|".join([_norm_key(po.purchase_order_number), _norm_key(po.supplier_name), _norm_key(po.buyer_name)])


class PurchaseOrderRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.session_factory = make_session_factory(engine)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _event(
        session: Session,
        entity_type: EntityType,
        entity_id: uuid.UUID,
        status: ProcessingStatus | str,
        message: str | None = None,
        details: dict | None = None,
        workflow_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        session.add(
            ProcessingEvent(
                entity_type=entity_type.value,
                entity_id=entity_id,
                status=status.value if isinstance(status, ProcessingStatus) else status,
                message=message,
                details=details,
                workflow_id=workflow_id,
                workflow_run_id=run_id,
            )
        )

    def email_outcome(self, session: Session, email: InboundEmail) -> dict[str, Any]:
        docs = session.scalars(select(Document).where(Document.email_id == email.id)).all()
        documents = []
        for doc in docs:
            po = doc.purchase_order
            documents.append(
                {
                    "document_id": str(doc.id),
                    "filename": doc.filename,
                    "status": doc.processing_status,
                    "purchase_order_id": str(po.id) if po else None,
                    "po_number": po.po_number if po else None,
                    "line_items_processed": len(po.items) if po else 0,
                }
            )
        return {"email_id": str(email.id), "status": email.processing_status, "documents": documents}

    # --------------------------------------------------------------- email
    def register_email(
        self, meta: EmailMetadata, workflow_id: str | None = None, run_id: str | None = None
    ) -> RegisteredEmail:
        values = dict(
            provider=meta.provider,
            provider_message_id=meta.provider_message_id,
            internet_message_id=meta.internet_message_id,
            thread_id=meta.thread_id,
            sender_email=meta.sender_email,
            sender_name=meta.sender_name,
            recipient_email=meta.recipient_emails,
            cc=meta.cc_emails,
            subject=meta.subject,
            email_body=meta.email_body,
            received_at=meta.received_at,
            attachments=[a.model_dump(mode="json") for a in meta.attachments],
            raw_storage_path=meta.raw_path,
            processing_status=ProcessingStatus.RECEIVED.value,
            workflow_id=workflow_id,
            workflow_run_id=run_id,
        )
        with session_scope(self.session_factory) as session:
            stmt = (
                insert(InboundEmail)
                .values(id=uuid.uuid4(), **values)
                .on_conflict_do_nothing(constraint="uq_inbound_emails_provider_message")
                .returning(InboundEmail.id)
            )
            inserted_id = session.execute(stmt).scalar_one_or_none()
            email = session.scalars(
                select(InboundEmail).where(
                    InboundEmail.provider == meta.provider,
                    InboundEmail.provider_message_id == meta.provider_message_id,
                )
            ).one()

            if inserted_id is not None:
                self._event(session, EntityType.EMAIL, email.id, ProcessingStatus.RECEIVED,
                            f"email from {meta.sender_email}: {meta.subject}", None, workflow_id, run_id)
                logger.info("Registered new email %s (%s)", email.id, meta.provider_message_id)
                return RegisteredEmail(email_id=str(email.id), status=ProcessingStatus.RECEIVED)

            if email.processing_status in TERMINAL_EMAIL_STATUSES:
                logger.info("Email %s already processed (%s); skipping", meta.provider_message_id, email.processing_status)
                self._event(session, EntityType.EMAIL, email.id, email.processing_status,
                            "duplicate delivery of an already-processed email ignored", None, workflow_id, run_id)
                return RegisteredEmail(
                    email_id=str(email.id),
                    status=ProcessingStatus(email.processing_status),
                    already_completed=True,
                    previous_result=self.email_outcome(session, email),
                )

            # Previously failed / interrupted: refresh metadata and reprocess.
            for key, value in values.items():
                setattr(email, key, value)
            email.error_message = None
            email.error_details = None
            self._event(session, EntityType.EMAIL, email.id, ProcessingStatus.RECEIVED,
                        "reprocessing previously incomplete email", None, workflow_id, run_id)
            logger.info("Reprocessing email %s (%s)", email.id, meta.provider_message_id)
            return RegisteredEmail(email_id=str(email.id), status=ProcessingStatus.RECEIVED)

    # ----------------------------------------------------------- document
    def register_document(
        self,
        email_id: str,
        attachment: DownloadedAttachment,
        document_type: DocumentType = DocumentType.PURCHASE_ORDER,
        workflow_id: str | None = None,
        run_id: str | None = None,
    ) -> RegisteredDocument:
        email_uuid = uuid.UUID(email_id)
        with session_scope(self.session_factory) as session:
            stmt = (
                insert(Document)
                .values(
                    id=uuid.uuid4(),
                    email_id=email_uuid,
                    attachment_index=attachment.attachment_index,
                    filename=attachment.filename,
                    mime_type=attachment.mime_type,
                    size_bytes=attachment.size_bytes,
                    file_hash=attachment.sha256,
                    storage_path=attachment.storage_path,
                    document_type=document_type.value,
                    processing_status=ProcessingStatus.DOCUMENT_DOWNLOADED.value,
                )
                .on_conflict_do_nothing(constraint="uq_documents_file_hash")
                .returning(Document.id)
            )
            inserted_id = session.execute(stmt).scalar_one_or_none()
            doc = session.scalars(select(Document).where(Document.file_hash == attachment.sha256)).one()

            if inserted_id is not None:
                self._event(session, EntityType.DOCUMENT, doc.id, ProcessingStatus.DOCUMENT_DOWNLOADED,
                            f"{attachment.filename} ({attachment.size_bytes} bytes, sha256={attachment.sha256[:12]}...)",
                            None, workflow_id, run_id)
                return RegisteredDocument(document_id=str(doc.id))

            po = doc.purchase_order
            if po is None and doc.processing_status == ProcessingStatus.DUPLICATE.value and doc.error_details:
                po_id = doc.error_details.get("duplicate_of_purchase_order_id")
                po = session.get(PurchaseOrderRecord, uuid.UUID(po_id)) if po_id else None
            if po is not None:
                logger.info("Attachment %s already processed as document %s (PO %s)", attachment.sha256[:12], doc.id, po.po_number)
                if doc.email_id != email_uuid:
                    self._event(session, EntityType.EMAIL, email_uuid, ProcessingStatus.DUPLICATE,
                                f"attachment {attachment.filename} already processed as document {doc.id}",
                                {"duplicate_of_document_id": str(doc.id)}, workflow_id, run_id)
                return RegisteredDocument(
                    document_id=str(doc.id),
                    is_duplicate=True,
                    duplicate_of_document_id=str(doc.id),
                    existing_purchase_order_id=str(po.id),
                    existing_po_number=po.po_number,
                    existing_line_items=len(po.items),
                )

            # Same file seen before but never completed: take it over and reprocess.
            doc.email_id = email_uuid
            doc.attachment_index = attachment.attachment_index
            doc.storage_path = attachment.storage_path
            doc.processing_status = ProcessingStatus.DOCUMENT_DOWNLOADED.value
            doc.error_message = None
            doc.error_details = None
            self._event(session, EntityType.DOCUMENT, doc.id, ProcessingStatus.DOCUMENT_DOWNLOADED,
                        "reprocessing previously incomplete document", None, workflow_id, run_id)
            return RegisteredDocument(document_id=str(doc.id))

    def store_parsed_document(
        self,
        document_id: str,
        *,
        parser: str,
        page_count: int | None,
        markdown: str,
        raw_output_path: str | None,
        workflow_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        with session_scope(self.session_factory) as session:
            doc = session.get(Document, uuid.UUID(document_id))
            if doc is None:
                raise LookupError(f"document {document_id} not found")
            doc.parser = parser
            doc.page_count = page_count
            doc.raw_extracted_text = markdown
            doc.raw_parser_output_path = raw_output_path
            doc.processing_status = ProcessingStatus.DOCUMENT_PARSED.value
            self._event(session, EntityType.DOCUMENT, doc.id, ProcessingStatus.DOCUMENT_PARSED,
                        f"parsed with {parser}: {page_count} page(s), {len(markdown)} chars", None, workflow_id, run_id)

    def store_extraction(
        self, document_id: str, extraction: ExtractionResult, workflow_id: str | None = None, run_id: str | None = None
    ) -> None:
        with session_scope(self.session_factory) as session:
            doc = session.get(Document, uuid.UUID(document_id))
            if doc is None:
                raise LookupError(f"document {document_id} not found")
            doc.extraction_json = extraction.model_dump(mode="json")
            doc.document_type = extraction.document_type.value
            doc.processing_status = ProcessingStatus.EXTRACTED.value
            po = extraction.purchase_order
            self._event(session, EntityType.DOCUMENT, doc.id, ProcessingStatus.EXTRACTED,
                        f"PO {po.purchase_order_number}: {len(po.line_items)} line item(s)",
                        {"warnings": extraction.warnings}, workflow_id, run_id)

    def store_validation(
        self, document_id: str, validation: ValidationResult, workflow_id: str | None = None, run_id: str | None = None
    ) -> None:
        with session_scope(self.session_factory) as session:
            doc = session.get(Document, uuid.UUID(document_id))
            if doc is None:
                raise LookupError(f"document {document_id} not found")
            doc.validation_json = validation.model_dump(mode="json", exclude={"purchase_order"})
            status = ProcessingStatus.VALIDATED if validation.is_valid else ProcessingStatus.FAILED
            doc.processing_status = status.value
            if not validation.is_valid:
                doc.error_message = "; ".join(f"{e.field}: {e.message}" for e in validation.errors)
                doc.error_details = {"validation_errors": [e.model_dump() for e in validation.errors]}
            self._event(session, EntityType.DOCUMENT, doc.id, status,
                        f"{len(validation.errors)} error(s), {len(validation.warnings)} warning(s)",
                        doc.validation_json, workflow_id, run_id)

    # ------------------------------------------------------ purchase order
    def save_purchase_order(
        self,
        document_id: str,
        validation: ValidationResult,
        workflow_id: str | None = None,
        run_id: str | None = None,
    ) -> SavedPurchaseOrder:
        try:
            return self._save_purchase_order(document_id, validation, workflow_id, run_id)
        except IntegrityError:
            # A concurrent run inserted the same PO between our check and insert; the
            # second attempt observes it and returns the existing row.
            logger.warning("Integrity race saving PO for document %s; re-checking", document_id)
            return self._save_purchase_order(document_id, validation, workflow_id, run_id)

    def _save_purchase_order(
        self, document_id: str, validation: ValidationResult, workflow_id: str | None, run_id: str | None
    ) -> SavedPurchaseOrder:
        po = validation.purchase_order
        if not po.purchase_order_number:
            raise ValueError("cannot save a purchase order without a PO number")
        key = purchase_order_dedupe_key(po)

        with session_scope(self.session_factory) as session:
            doc = session.get(Document, uuid.UUID(document_id), with_for_update=True)
            if doc is None:
                raise LookupError(f"document {document_id} not found")

            if doc.purchase_order is not None:
                existing = doc.purchase_order
                logger.info("Document %s already has PO %s; not inserting again", document_id, existing.po_number)
                return SavedPurchaseOrder(
                    purchase_order_id=str(existing.id), po_number=existing.po_number,
                    line_items_processed=len(existing.items), created=False,
                    duplicate_reason="purchase order already saved for this document",
                )

            existing = session.scalars(select(PurchaseOrderRecord).where(PurchaseOrderRecord.dedupe_key == key)).first()
            if existing is not None:
                doc.processing_status = ProcessingStatus.DUPLICATE.value
                doc.error_message = f"PO {po.purchase_order_number} already exists (document {existing.document_id})"
                doc.error_details = {"duplicate_of_purchase_order_id": str(existing.id), "dedupe_key": key}
                self._event(session, EntityType.DOCUMENT, doc.id, ProcessingStatus.DUPLICATE,
                            doc.error_message, doc.error_details, workflow_id, run_id)
                logger.info("PO %s already stored as %s; marking document duplicate", po.purchase_order_number, existing.id)
                return SavedPurchaseOrder(
                    purchase_order_id=str(existing.id), po_number=existing.po_number,
                    line_items_processed=len(existing.items), created=False, is_duplicate=True,
                    duplicate_reason="purchase order number already exists for this supplier/buyer",
                )

            record = PurchaseOrderRecord(
                id=uuid.uuid4(),
                document_id=doc.id,
                email_id=doc.email_id,
                po_number=po.purchase_order_number,
                dedupe_key=key,
                supplier_name=po.supplier_name,
                supplier_email=po.supplier_email,
                supplier_address=po.supplier_address,
                buyer_name=po.buyer_name,
                buyer_address=po.buyer_address,
                order_date=po.order_date,
                requested_delivery_date=po.requested_delivery_date,
                currency=po.currency,
                subtotal=po.subtotal,
                tax=po.tax,
                shipping=po.shipping,
                total_amount=po.total_amount,
                payment_terms=po.payment_terms,
                shipping_terms=po.shipping_terms,
                ship_to_address=po.ship_to_address,
                bill_to_address=po.bill_to_address,
                validation_warnings=[w.model_dump() for w in validation.warnings],
                items=[
                    PurchaseOrderItem(
                        sequence=seq,
                        line_number=item.line_number,
                        part_number=item.part_number,
                        part_name=item.part_name,
                        quantity=item.quantity,
                        unit_of_measure=item.unit_of_measure,
                        unit_price=item.unit_price,
                        line_total=item.line_total,
                        requested_delivery_date=item.requested_delivery_date,
                    )
                    for seq, item in enumerate(po.line_items, start=1)
                ],
            )
            session.add(record)
            doc.processing_status = ProcessingStatus.SAVED.value
            doc.error_message = None
            doc.error_details = None
            session.flush()
            self._event(session, EntityType.DOCUMENT, doc.id, ProcessingStatus.SAVED,
                        f"saved PO {record.po_number} with {len(record.items)} line item(s)",
                        {"purchase_order_id": str(record.id)}, workflow_id, run_id)
            logger.info("Saved PO %s (%s) with %d items", record.po_number, record.id, len(record.items))
            return SavedPurchaseOrder(
                purchase_order_id=str(record.id), po_number=record.po_number, line_items_processed=len(record.items)
            )

    # -------------------------------------------------------------- status
    def update_status(self, update: StatusUpdate) -> None:
        model = InboundEmail if update.entity_type == EntityType.EMAIL else Document
        with session_scope(self.session_factory) as session:
            entity = session.get(model, uuid.UUID(update.entity_id))
            if entity is None:
                raise LookupError(f"{update.entity_type.value} {update.entity_id} not found")
            entity.processing_status = update.status.value
            if update.status == ProcessingStatus.FAILED or update.error_message:
                entity.error_message = update.error_message
                entity.error_details = update.error_details
            elif update.status in (ProcessingStatus.SAVED, ProcessingStatus.IGNORED):
                entity.error_message = None
                entity.error_details = None
            self._event(session, update.entity_type, entity.id, update.status, update.error_message,
                        update.error_details, update.workflow_id, update.run_id)
        logger.info("%s %s -> %s %s", update.entity_type.value, update.entity_id, update.status.value,
                    f"({update.error_message})" if update.error_message else "")
