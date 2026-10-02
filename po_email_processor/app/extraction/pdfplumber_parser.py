"""Lightweight fallback parser (pdfplumber).

Docling is the primary parser. This fallback exists for environments where the
Docling layout models cannot be downloaded (air-gapped CI, restricted
networks). It produces the same :class:`ParsedDocument` shape: text outside
tables as text blocks, ruled tables as cell grids, plus a Markdown rendering.
Select it with ``DOCUMENT_PARSER=pdfplumber``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.extraction.base import DocumentParseError, DocumentParser
from app.models.schemas import ParsedDocument, ParsedTable

logger = logging.getLogger(__name__)


def table_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [[(c or "").replace("\n", " ").replace("|", "\\|") for c in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join(lines)


class PdfPlumberParser(DocumentParser):
    name = "pdfplumber"

    def parse(self, file_path: Path) -> tuple[ParsedDocument, dict | None]:
        import pdfplumber
        from pdfminer.pdfparser import PDFSyntaxError

        text_blocks: list[str] = []
        tables: list[ParsedTable] = []
        md_parts: list[str] = []
        try:
            with pdfplumber.open(str(file_path)) as pdf:
                page_count = len(pdf.pages)
                for page_no, page in enumerate(pdf.pages, start=1):
                    found = page.find_tables()
                    bboxes = [t.bbox for t in found]

                    def outside_tables(obj, _bboxes=bboxes) -> bool:
                        if obj.get("object_type") != "char":
                            return True
                        x, top = obj["x0"], obj["top"]
                        return not any(b[0] <= x <= b[2] and b[1] <= top <= b[3] for b in _bboxes)

                    text = page.filter(outside_tables).extract_text() or ""
                    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                    text_blocks.extend(lines)
                    md_parts.extend(lines)
                    for table in found:
                        rows = [[(c or "").strip() for c in row] for row in table.extract()]
                        rows = [r for r in rows if any(r)]
                        if rows:
                            tables.append(ParsedTable(page_no=page_no, rows=rows))
                            md_parts.append(table_to_markdown(rows))
        except PDFSyntaxError as exc:
            raise DocumentParseError(f"Malformed PDF {file_path.name}: {exc}") from exc
        except Exception as exc:
            raise DocumentParseError(f"Could not parse {file_path.name}: {exc}") from exc

        if not text_blocks and not tables:
            raise DocumentParseError(f"No extractable content in {file_path.name} (scanned PDF?)")

        logger.info("pdfplumber parsed %s: pages=%d tables=%d", file_path.name, page_count, len(tables))
        return (
            ParsedDocument(
                parser=self.name,
                page_count=page_count,
                markdown="\n\n".join(md_parts),
                text_blocks=text_blocks,
                tables=tables,
            ),
            None,
        )

