"""PostgreSQL activities. Each one is idempotent, so Temporal retries are safe."""

from __future__ import annotations

import logging

from temporalio import activity

from app.database.repository import PurchaseOrderRepository
from app.models.schemas import (
    DocumentValidationRequest,
    EmailMetadata,
    ExtractionResult,
    ParsedDocumentRef,
    RegisteredDocument,
    RegisteredEmail,
    RegisterDocumentRequest,
    SavedPurchaseOrder,
    StatusUpdate,
)
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)


def _wf() -> tuple[str | None, str | None]:
    info = activity.info()
    return info.workflow_id, info.workflow_run_id


class DatabaseActivities:
    def __init__(self, repository: PurchaseOrderRepository) -> None:
        self.repo = repository

    @activity.defn(name="register_email")
    def register_email(self, meta: EmailMetadata) -> RegisteredEmail:
        wf_id, run_id = _wf()
        return self.repo.register_email(meta, wf_id, run_id)

    @activity.defn(name="register_document")
    def register_document(self, req: RegisterDocumentRequest) -> RegisteredDocument:
        wf_id, run_id = _wf()
        result = self.repo.register_document(req.email_id, req.attachment, req.document_type, wf_id, run_id)
        logger.info("Document %s registered (duplicate=%s)", result.document_id, result.is_duplicate)
        return result

    @activity.defn(name="store_parsed_document")
    def store_parsed_document(self, ref: ParsedDocumentRef) -> None:
        wf_id, run_id = _wf()
        self.repo.store_parsed_document(
            ref.document_id,
            parser=ref.parser,
            page_count=ref.page_count,
            markdown=FileStore.read_text(ref.markdown_path),
            raw_output_path=ref.raw_output_path,
            workflow_id=wf_id,
            run_id=run_id,
        )

    @activity.defn(name="store_extraction_result")
    def store_extraction_result(self, extraction: ExtractionResult) -> None:
        wf_id, run_id = _wf()
        if extraction.document_id is None:
            raise ValueError("extraction result has no document_id")
        self.repo.store_extraction(extraction.document_id, extraction, wf_id, run_id)

    @activity.defn(name="store_validation_result")
    def store_validation_result(self, req: DocumentValidationRequest) -> None:
        wf_id, run_id = _wf()
        self.repo.store_validation(req.document_id, req.validation, wf_id, run_id)

    @activity.defn(name="save_purchase_order_to_postgres")
    def save_purchase_order_to_postgres(self, req: DocumentValidationRequest) -> SavedPurchaseOrder:
        wf_id, run_id = _wf()
        return self.repo.save_purchase_order(req.document_id, req.validation, wf_id, run_id)

    @activity.defn(name="update_processing_status")
    def update_processing_status(self, update: StatusUpdate) -> None:
        wf_id, run_id = _wf()
        update = update.model_copy(update={"workflow_id": update.workflow_id or wf_id, "run_id": update.run_id or run_id})
        self.repo.update_status(update)
