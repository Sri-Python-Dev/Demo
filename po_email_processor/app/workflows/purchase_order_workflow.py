"""PurchaseOrderEmailWorkflow - orchestrates email -> PO extraction -> PostgreSQL.

Deterministic by construction: this module performs no I/O, reads no clock and
uses no randomness. Every side effect (email API, files, Docling, database)
lives in an activity with its own timeout and retry policy.

    fetch_email -> extract_email_metadata -> register_email
        for each PDF attachment:
            download_po_attachment -> register_document
            -> parse_document_with_docling -> store_parsed_document
            -> extract_purchase_order -> store_extraction_result
            -> validate_purchase_order -> store_validation_result
            -> save_purchase_order_to_postgres
    -> update_processing_status -> acknowledge_email
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from app.activities.database_activities import DatabaseActivities
    from app.activities.document_activities import DocumentActivities
    from app.activities.email_activities import EmailActivities
    from app.activities.errors import NON_RETRYABLE_ERROR_TYPES, VALIDATION_ERROR
    from app.models.schemas import (
        AttachmentInfo,
        AttachmentRequest,
        DocumentOutcome,
        DocumentValidationRequest,
        EntityType,
        FetchedEmail,
        ParseRequest,
        ProcessingStatus,
        RegisterDocumentRequest,
        StatusUpdate,
        WorkflowInput,
        WorkflowResult,
    )

# --------------------------------------------------------------------------
# Timeouts & retry policies, per kind of dependency
# --------------------------------------------------------------------------
EMAIL_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=1),
    maximum_attempts=5,
    non_retryable_error_types=NON_RETRYABLE_ERROR_TYPES,
)
DB_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=10,
    non_retryable_error_types=NON_RETRYABLE_ERROR_TYPES,
)
PARSE_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=5),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(minutes=2),
    maximum_attempts=3,
    non_retryable_error_types=NON_RETRYABLE_ERROR_TYPES,
)
EXTRACT_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=3,
    non_retryable_error_types=NON_RETRYABLE_ERROR_TYPES,
)

EMAIL_TIMEOUT = timedelta(minutes=2)
DB_TIMEOUT = timedelta(seconds=30)
# Docling's first run downloads layout models; later runs take seconds per document.
PARSE_TIMEOUT = timedelta(minutes=10)
EXTRACT_TIMEOUT = timedelta(minutes=2)


def describe_error(err: BaseException) -> tuple[str, str]:
    """(error_type, message) from an ActivityError/ApplicationError chain."""
    cause: BaseException | None = err
    while isinstance(cause, ActivityError) and cause.cause is not None:
        cause = cause.cause
    if isinstance(cause, ApplicationError):
        return cause.type or "ApplicationError", cause.message
    return type(cause).__name__, str(cause)


@workflow.defn(name="PurchaseOrderEmailWorkflow")
class PurchaseOrderEmailWorkflow:
    def __init__(self) -> None:
        self._stage = "STARTED"
        self._documents: list[DocumentOutcome] = []

    @workflow.query
    def progress(self) -> dict[str, Any]:
        return {"stage": self._stage, "documents": [d.model_dump(mode="json") for d in self._documents]}

    # ------------------------------------------------------------ helpers
    async def _email(self, fn, arg):
        return await workflow.execute_activity_method(
            fn, arg, start_to_close_timeout=EMAIL_TIMEOUT, retry_policy=EMAIL_RETRY
        )

    async def _db(self, fn, arg):
        return await workflow.execute_activity_method(fn, arg, start_to_close_timeout=DB_TIMEOUT, retry_policy=DB_RETRY)

    async def _status(
        self,
        entity_type: EntityType,
        entity_id: str,
        status: ProcessingStatus,
        error: str | None = None,
        details: dict | None = None,
    ) -> None:
        await self._db(
            DatabaseActivities.update_processing_status,
            StatusUpdate(
                entity_type=entity_type, entity_id=entity_id, status=status, error_message=error, error_details=details
            ),
        )

    # ---------------------------------------------------------------- run
    @workflow.run
    async def run(self, inp: WorkflowInput) -> WorkflowResult:
        log = workflow.logger
        log.info("Processing %s message %s", inp.provider, inp.provider_message_id)

        self._stage = "FETCH_EMAIL"
        fetched: FetchedEmail = await self._email(EmailActivities.fetch_email, inp)
        self._stage = "PARSE_EMAIL"
        meta = await self._email(EmailActivities.extract_email_metadata, fetched)
        registered = await self._db(DatabaseActivities.register_email, meta)
        email_id = registered.email_id

        if registered.already_completed:
            # Duplicate delivery of an email we already finished: report, don't reprocess.
            self._stage = "DUPLICATE_EMAIL"
            await self._email(EmailActivities.acknowledge_email, inp)
            prev = registered.previous_result or {}
            docs = [
                DocumentOutcome(
                    filename=d["filename"], status=ProcessingStatus.DUPLICATE, document_id=d["document_id"],
                    purchase_order_id=d["purchase_order_id"], po_number=d["po_number"],
                    line_items_processed=d["line_items_processed"],
                )
                for d in prev.get("documents", [])
            ]
            log.info("Email %s already processed; returning previous result", inp.provider_message_id)
            return self._result("DUPLICATE", email_id, docs)

        try:
            pdfs = meta.pdf_attachments()
            if not pdfs:
                self._stage = "IGNORED"
                await self._status(EntityType.EMAIL, email_id, ProcessingStatus.IGNORED, "no PDF attachment found")
                await self._email(EmailActivities.acknowledge_email, inp)
                return self._result("IGNORED", email_id, [])

            await self._status(EntityType.EMAIL, email_id, ProcessingStatus.EMAIL_PARSED)
            for attachment in pdfs:
                outcome = await self._process_document(email_id, fetched, attachment)
                self._documents.append(outcome)
        except ActivityError as err:
            error_type, message = describe_error(err)
            await self._status(EntityType.EMAIL, email_id, ProcessingStatus.FAILED, message, {"error_type": error_type})
            raise

        failed = [d for d in self._documents if d.status == ProcessingStatus.FAILED]
        # Terminal either way: acknowledge so pollers don't pick the message up again.
        # Failures stay in PostgreSQL (status FAILED + error details) for review / replay.
        self._stage = "ACKNOWLEDGE"
        if failed:
            summary = "; ".join(f"{d.filename}: {d.error}" for d in failed)
            await self._status(
                EntityType.EMAIL, email_id, ProcessingStatus.FAILED, summary,
                {"failed_documents": [d.model_dump(mode="json") for d in failed]},
            )
            await self._email(EmailActivities.acknowledge_email, inp)
            self._stage = "FAILED"
            raise ApplicationError(
                f"{len(failed)} of {len(self._documents)} document(s) failed: {summary}",
                type="DocumentProcessingFailed",
                non_retryable=True,
            )

        all_duplicates = all(d.status == ProcessingStatus.DUPLICATE for d in self._documents)
        final = ProcessingStatus.DUPLICATE if all_duplicates else ProcessingStatus.SAVED
        await self._status(EntityType.EMAIL, email_id, final)
        await self._email(EmailActivities.acknowledge_email, inp)
        self._stage = "COMPLETED"
        return self._result("DUPLICATE" if all_duplicates else "COMPLETED", email_id, self._documents)

    async def _process_document(self, email_id: str, fetched: FetchedEmail, attachment: AttachmentInfo) -> DocumentOutcome:
        filename = attachment.filename or f"attachment-{attachment.index}"
        document_id: str | None = None
        stage = "DOWNLOAD_ATTACHMENT"
        try:
            self._stage = f"{stage}:{filename}"
            downloaded = await self._email(
                EmailActivities.download_po_attachment,
                AttachmentRequest(
                    provider_message_id=fetched.provider_message_id, raw_path=fetched.raw_path,
                    attachment_index=attachment.index,
                ),
            )
            doc = await self._db(
                DatabaseActivities.register_document, RegisterDocumentRequest(email_id=email_id, attachment=downloaded)
            )
            document_id = doc.document_id
            if doc.is_duplicate:
                workflow.logger.info("Attachment %s duplicates document %s", filename, doc.duplicate_of_document_id)
                return DocumentOutcome(
                    filename=filename, status=ProcessingStatus.DUPLICATE, document_id=doc.document_id,
                    purchase_order_id=doc.existing_purchase_order_id, po_number=doc.existing_po_number,
                    line_items_processed=doc.existing_line_items or 0,
                )

            stage = "PARSE_DOCUMENT"
            self._stage = f"{stage}:{filename}"
            parsed_ref = await workflow.execute_activity_method(
                DocumentActivities.parse_document_with_docling,
                ParseRequest(document_id=document_id, storage_path=downloaded.storage_path, filename=filename),
                start_to_close_timeout=PARSE_TIMEOUT,
                retry_policy=PARSE_RETRY,
            )
            await self._db(DatabaseActivities.store_parsed_document, parsed_ref)

            stage = "EXTRACT_PURCHASE_ORDER"
            self._stage = f"{stage}:{filename}"
            extraction = await workflow.execute_activity_method(
                DocumentActivities.extract_purchase_order,
                parsed_ref,
                start_to_close_timeout=EXTRACT_TIMEOUT,
                retry_policy=EXTRACT_RETRY,
            )
            await self._db(DatabaseActivities.store_extraction_result, extraction)

            stage = "VALIDATE_PURCHASE_ORDER"
            self._stage = f"{stage}:{filename}"
            validation = await workflow.execute_activity_method(
                DocumentActivities.validate_purchase_order,
                extraction,
                start_to_close_timeout=EXTRACT_TIMEOUT,
                retry_policy=EXTRACT_RETRY,
            )
            await self._db(
                DatabaseActivities.store_validation_result,
                DocumentValidationRequest(document_id=document_id, validation=validation),
            )
            if not validation.is_valid:
                # Permanent: re-running the same document cannot fix its content.
                raise ApplicationError(
                    "; ".join(f"{e.field}: {e.message}" for e in validation.errors),
                    type=VALIDATION_ERROR,
                    non_retryable=True,
                )

            stage = "SAVE_PURCHASE_ORDER"
            self._stage = f"{stage}:{filename}"
            saved = await self._db(
                DatabaseActivities.save_purchase_order_to_postgres,
                DocumentValidationRequest(document_id=document_id, validation=validation),
            )
            return DocumentOutcome(
                filename=filename,
                status=ProcessingStatus.DUPLICATE if saved.is_duplicate else ProcessingStatus.SAVED,
                document_id=document_id,
                purchase_order_id=saved.purchase_order_id,
                po_number=saved.po_number,
                line_items_processed=saved.line_items_processed,
            )
        except (ActivityError, ApplicationError) as err:
            error_type, message = describe_error(err)
            workflow.logger.warning("Document %s failed at %s: %s: %s", filename, stage, error_type, message)
            if document_id is not None:
                await self._status(
                    EntityType.DOCUMENT, document_id, ProcessingStatus.FAILED, f"{error_type}: {message}",
                    {"error_type": error_type, "stage": stage},
                )
            return DocumentOutcome(
                filename=filename, status=ProcessingStatus.FAILED, document_id=document_id,
                error=f"{error_type} at {stage}: {message}",
            )

    @staticmethod
    def _result(status: str, email_id: str, documents: list[DocumentOutcome]) -> WorkflowResult:
        primary = next((d for d in documents if d.purchase_order_id), None)
        return WorkflowResult(
            status=status,
            email_id=email_id,
            document_id=primary.document_id if primary else None,
            purchase_order_id=primary.purchase_order_id if primary else None,
            po_number=primary.po_number if primary else None,
            line_items_processed=sum(d.line_items_processed for d in documents),
            documents=list(documents),
        )
