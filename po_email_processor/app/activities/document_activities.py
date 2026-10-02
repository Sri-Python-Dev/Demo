"""Document activities: Docling parsing, structured extraction and validation."""

from __future__ import annotations

import logging
from pathlib import Path

from temporalio import activity

from app.activities.errors import DOCUMENT_PARSE_ERROR, EXTRACTION_ERROR, permanent
from app.extraction.base import DocumentParseError, DocumentParser, ExtractionError
from app.extraction.factory import get_extractor
from app.extraction.validation import validate_purchase_order
from app.models.schemas import (
    DocumentType,
    ExtractionResult,
    ParsedDocument,
    ParsedDocumentRef,
    ParseRequest,
    ValidationResult,
)
from app.storage.file_store import FileStore

logger = logging.getLogger(__name__)


class DocumentActivities:
    def __init__(self, parser: DocumentParser, store: FileStore, date_order: str = "MDY") -> None:
        self.parser = parser
        self.store = store
        self.date_order = date_order

    @activity.defn(name="parse_document_with_docling")
    def parse_document_with_docling(self, req: ParseRequest) -> ParsedDocumentRef:
        path = Path(req.storage_path)
        if not path.exists():
            raise permanent(DOCUMENT_PARSE_ERROR, f"stored attachment missing: {path}")
        logger.info("Parsing document %s (%s) with %s", req.document_id, req.filename, self.parser.name)
        try:
            parsed, raw_output = self.parser.parse(path)
        except DocumentParseError as exc:
            raise permanent(DOCUMENT_PARSE_ERROR, f"{req.filename or path.name}: {exc}", document_id=req.document_id) from exc

        parsed.document_id = req.document_id
        parsed_path = self.store.save_text("parsed", f"{req.document_id}.parsed.json", parsed.model_dump_json(indent=2))
        markdown_path = self.store.save_text("parsed", f"{req.document_id}.md", parsed.markdown)
        raw_path = self.store.save_json("parsed", f"{req.document_id}.{self.parser.name}.json", raw_output) if raw_output else None
        logger.info(
            "Parsed document %s: pages=%s tables=%d chars=%d", req.document_id, parsed.page_count,
            len(parsed.tables), len(parsed.markdown),
        )
        return ParsedDocumentRef(
            document_id=req.document_id,
            parser=parsed.parser,
            parsed_path=str(parsed_path),
            markdown_path=str(markdown_path),
            raw_output_path=str(raw_path) if raw_path else None,
            page_count=parsed.page_count,
            table_count=len(parsed.tables),
            text_length=len(parsed.markdown),
        )

    @activity.defn(name="extract_purchase_order")
    def extract_purchase_order(self, ref: ParsedDocumentRef) -> ExtractionResult:
        parsed = ParsedDocument.model_validate_json(self.store.read_text(ref.parsed_path))
        # Today every inbound PDF is treated as a PO. A document classifier activity can
        # later decide the type and pick the extractor here, without workflow changes.
        extractor = get_extractor(DocumentType.PURCHASE_ORDER, date_order=self.date_order)
        try:
            result = extractor.extract(parsed)
        except ExtractionError as exc:
            raise permanent(EXTRACTION_ERROR, exc, document_id=ref.document_id) from exc
        result.document_id = ref.document_id
        po = result.purchase_order
        logger.info(
            "Document %s -> PO %s, supplier=%s, %d line items, warnings=%s",
            ref.document_id, po.purchase_order_number, po.supplier_name, len(po.line_items), result.warnings,
        )
        return result

    @activity.defn(name="validate_purchase_order")
    def validate_purchase_order(self, extraction: ExtractionResult) -> ValidationResult:
        result = validate_purchase_order(extraction.purchase_order)
        logger.info(
            "Validation for document %s: valid=%s errors=%s warnings=%d",
            extraction.document_id, result.is_valid, [e.message for e in result.errors], len(result.warnings),
        )
        return result
