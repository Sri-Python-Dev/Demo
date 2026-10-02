"""Interfaces for the document processing layer.

The Temporal workflow only knows about activities; activities only know about
these interfaces. Swapping Docling for another parser, or the rule-based PO
extractor for an LLM-based one, therefore never touches workflow code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from app.models.schemas import DocumentType, ExtractionResult, ParsedDocument


class DocumentParseError(Exception):
    """The document could not be parsed (corrupt, encrypted, unsupported). Permanent."""


class ExtractionError(Exception):
    """Structured extraction failed in a way that retrying will not fix."""


class DocumentParser(ABC):
    name: str = "base"

    @abstractmethod
    def parse(self, file_path: Path) -> tuple[ParsedDocument, dict | None]:
        """Return the normalised parse plus the parser's raw output (for audit), if any."""


class DocumentExtractor(ABC):
    """Turns a parsed document into a typed extraction result for one document type."""

    name: str = "base"
    version: str = "0"
    document_type: DocumentType = DocumentType.UNKNOWN

    @abstractmethod
    def extract(self, parsed: ParsedDocument) -> ExtractionResult: ...
