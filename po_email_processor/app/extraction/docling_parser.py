"""Docling-based PDF parsing.

Docling's ``DocumentConverter`` runs layout analysis and table-structure
recognition, giving us real table cells instead of guessing columns from raw
PDF text. We keep three outputs:

* the Markdown export (human-readable, stored in PostgreSQL for audit),
* the full Docling JSON (``export_to_dict``) for debugging / re-processing,
* a parser-agnostic :class:`ParsedDocument` (text blocks + table grids) that the
  extractors consume.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from app.extraction.base import DocumentParseError, DocumentParser
from app.models.schemas import ParsedDocument, ParsedTable

logger = logging.getLogger(__name__)

# Docling labels that carry document text we care about.
_TEXT_LABELS = {
    "title", "section_header", "text", "paragraph", "list_item", "caption",
    "key_value_region", "form", "page_header", "page_footer", "footnote",
}


def docling_document_to_parsed(doc: Any, parser_name: str = "docling") -> ParsedDocument:
    """Convert a ``DoclingDocument`` into our parser-agnostic representation."""
    text_blocks: list[str] = []
    for item in getattr(doc, "texts", []) or []:
        label = str(getattr(getattr(item, "label", None), "value", getattr(item, "label", ""))).lower()
        text = (getattr(item, "text", "") or "").strip()
        if text and (label in _TEXT_LABELS or not label):
            text_blocks.append(text)

    tables: list[ParsedTable] = []
    for table in getattr(doc, "tables", []) or []:
        grid = getattr(table.data, "grid", None) or []
        rows = [[(cell.text or "").strip() for cell in row] for row in grid]
        rows = [r for r in rows if any(c for c in r)]
        if not rows:
            continue
        page_no = table.prov[0].page_no if getattr(table, "prov", None) else None
        tables.append(ParsedTable(page_no=page_no, rows=rows))

    pages = getattr(doc, "pages", None) or {}
    return ParsedDocument(
        parser=parser_name,
        page_count=len(pages) or None,
        markdown=doc.export_to_markdown(),
        text_blocks=text_blocks,
        tables=tables,
    )


class DoclingParser(DocumentParser):
    name = "docling"

    _converter_lock = threading.Lock()
    _converter: Any = None

    def __init__(self, do_ocr: bool = False, do_table_structure: bool = True) -> None:
        self.do_ocr = do_ocr
        self.do_table_structure = do_table_structure

    def _get_converter(self) -> Any:
        # Building a converter loads ML models; do it once per worker process.
        with self._converter_lock:
            if DoclingParser._converter is None:
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import PdfPipelineOptions
                from docling.document_converter import DocumentConverter, PdfFormatOption

                options = PdfPipelineOptions(do_ocr=self.do_ocr, do_table_structure=self.do_table_structure)
                DoclingParser._converter = DocumentConverter(
                    format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
                )
                logger.info("Initialised Docling DocumentConverter (ocr=%s)", self.do_ocr)
            return DoclingParser._converter

    def parse(self, file_path: Path) -> tuple[ParsedDocument, dict | None]:
        converter = self._get_converter()
        from docling.datamodel.base_models import ConversionStatus

        logger.info("Docling converting %s", file_path)
        try:
            result = converter.convert(str(file_path), raises_on_error=False)
        except Exception as exc:  # docling raises various errors for corrupt input
            raise DocumentParseError(f"Docling failed to convert {file_path.name}: {exc}") from exc

        if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
            errors = "; ".join(str(getattr(e, "error_message", e)) for e in (result.errors or []))
            raise DocumentParseError(f"Docling conversion status {result.status}: {errors or 'no details'}")

        parsed = docling_document_to_parsed(result.document, self.name)
        if not parsed.markdown.strip() and not parsed.tables:
            raise DocumentParseError(f"Docling produced no content for {file_path.name} (scanned PDF without OCR?)")
        logger.info(
            "Docling parsed %s: pages=%s tables=%d text_blocks=%d",
            file_path.name, parsed.page_count, len(parsed.tables), len(parsed.text_blocks),
        )
        return parsed, result.document.export_to_dict()
