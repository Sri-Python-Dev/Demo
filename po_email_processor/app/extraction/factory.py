"""Factories that pick parser/extractor implementations from configuration.

Adding a new document family (Supplier Quote, RFQ, ASN ...) means registering
another :class:`DocumentExtractor` here; the workflow is unchanged.
"""

from __future__ import annotations

from app.extraction.base import DocumentExtractor, DocumentParser
from app.models.schemas import DocumentType


def get_parser(name: str, *, do_ocr: bool = False) -> DocumentParser:
    if name == "docling":
        from app.extraction.docling_parser import DoclingParser

        return DoclingParser(do_ocr=do_ocr)
    if name == "pdfplumber":
        from app.extraction.pdfplumber_parser import PdfPlumberParser

        return PdfPlumberParser()
    raise ValueError(f"Unknown document parser: {name!r}")


def get_extractor(document_type: DocumentType, *, date_order: str = "MDY") -> DocumentExtractor:
    if document_type == DocumentType.PURCHASE_ORDER:
        from app.extraction.po_extractor import RuleBasedPurchaseOrderExtractor

        return RuleBasedPurchaseOrderExtractor(date_order=date_order)
    raise ValueError(f"No extractor registered for document type {document_type.value}")
