"""Temporal worker: hosts PurchaseOrderEmailWorkflow and all of its activities.

    python worker.py
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.worker import Worker

from app.activities.database_activities import DatabaseActivities
from app.activities.document_activities import DocumentActivities
from app.activities.email_activities import EmailActivities
from app.config import Settings, get_settings
from app.database.connection import get_engine
from app.database.repository import PurchaseOrderRepository
from app.email.factory import get_email_provider
from app.extraction.factory import get_parser
from app.logging_config import configure_logging
from app.storage.file_store import FileStore
from app.temporal_client import connect
from app.workflows.purchase_order_workflow import PurchaseOrderEmailWorkflow

logger = logging.getLogger("worker")


def build_activities(settings: Settings) -> list:
    store = FileStore(settings.storage_dir)
    email = EmailActivities(get_email_provider(settings), store)
    documents = DocumentActivities(
        get_parser(settings.document_parser, do_ocr=settings.docling_do_ocr), store, settings.date_order
    )
    database = DatabaseActivities(PurchaseOrderRepository(get_engine()))
    return [
        email.fetch_email,
        email.extract_email_metadata,
        email.download_po_attachment,
        email.acknowledge_email,
        documents.parse_document_with_docling,
        documents.extract_purchase_order,
        documents.validate_purchase_order,
        database.register_email,
        database.register_document,
        database.store_parsed_document,
        database.store_extraction_result,
        database.store_validation_result,
        database.save_purchase_order_to_postgres,
        database.update_processing_status,
    ]


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    client = await connect(settings)
    # Activities are synchronous (SQLAlchemy, Docling), so they run in a thread pool.
    with ThreadPoolExecutor(max_workers=8) as executor:
        worker = Worker(
            client,
            task_queue=settings.temporal_task_queue,
            workflows=[PurchaseOrderEmailWorkflow],
            activities=build_activities(settings),
            activity_executor=executor,
            max_concurrent_activities=8,
        )
        logger.info(
            "Worker started: temporal=%s namespace=%s queue=%s provider=%s parser=%s",
            settings.temporal_address, settings.temporal_namespace, settings.temporal_task_queue,
            settings.email_provider, settings.document_parser,
        )
        await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
