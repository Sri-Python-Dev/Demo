"""Docling parsing tests.

The mapping DoclingDocument -> ParsedDocument (and extraction on top of it) is
tested with hand-built DoclingDocuments that mimic Docling's output style:
multi-line cells joined with spaces, key/value blocks recognised as tables,
paragraph-level text items, and a table split across pages. These run
everywhere. Real PDF conversion needs Docling's layout models and runs with
RUN_DOCLING_TESTS=1.
"""

from __future__ import annotations

import pytest

from app.evaluation.golden import evaluate, load_expected
from tests.conftest import run_docling

docling_core = pytest.importorskip("docling_core.types.doc")
from docling_core.types.doc import DocItemLabel, DoclingDocument, ProvenanceItem, TableCell, TableData  # noqa: E402
from docling_core.types.doc.base import BoundingBox  # noqa: E402

from app.extraction.docling_parser import docling_document_to_parsed  # noqa: E402


def _table(doc: DoclingDocument, rows: list[list[str]], page_no: int = 1) -> None:
    cells = [
        TableCell(
            text=text, start_row_offset_idx=r, end_row_offset_idx=r + 1,
            start_col_offset_idx=c, end_col_offset_idx=c + 1, column_header=(r == 0),
        )
        for r, row in enumerate(rows)
        for c, text in enumerate(row)
    ]
    prov = ProvenanceItem(page_no=page_no, bbox=BoundingBox(l=0, t=0, r=1, b=1), charspan=(0, 0))
    doc.add_table(data=TableData(num_rows=len(rows), num_cols=len(rows[0]), table_cells=cells), prov=prov)


def acme_like_docling_document() -> DoclingDocument:
    doc = DoclingDocument(name="acme")
    doc.add_title(text="PURCHASE ORDER")
    doc.add_text(label=DocItemLabel.TEXT, text="Acme Manufacturing Inc. 1200 Industrial Parkway Columbus, OH 43215, USA")
    _table(doc, [
        ["Purchase Order Number:", "PO-100245"],
        ["Order Date:", "03/15/2026"],
        ["Requested Delivery Date:", "04/10/2026"],
        ["Currency:", "USD"],
        ["Payment Terms:", "Net 30"],
        ["Incoterms:", "FOB Destination"],
    ])
    _table(doc, [
        ["Supplier:", "Ship To:", "Bill To:"],
        [
            "ABC Components Ltd. 45 Harbor Road Cleveland, OH 44114, USA orders@abccomponents.com",
            "Acme Manufacturing - Plant 2 88 Factory Lane Dayton, OH 45402, USA",
            "Acme Manufacturing Inc. Accounts Payable 1200 Industrial Parkway Columbus, OH 43215, USA",
        ],
    ])
    _table(doc, [
        ["Line", "Part Number", "Description", "Qty", "UOM", "Unit Price", "Line Total"],
        ["1", "ABC-123", "Bearing Assembly", "100", "EA", "$25.50", "$2,550.00"],
        ["2", "ABC-456", "Hydraulic Pump Seal Kit", "40", "EA", "$88.75", "$3,550.00"],
        ["3", "XYZ-789", "Stainless Steel Bolt M8x40", "2,000", "EA", "$0.42", "$840.00"],
        ["4", "GSK-220", "Gasket Sheet 1mm Neoprene", "25", "M", "$31.20", "$780.00"],
        ["5", "MTR-010", "Servo Motor 750W", "6", "EA", "$1,155.00", "$6,930.00"],
    ])
    _table(doc, [
        ["Subtotal:", "$14,650.00"],
        ["Sales Tax (7.25%):", "$1,062.12"],
        ["Shipping:", "$350.00"],
        ["Total Amount:", "$16,062.12"],
    ])
    doc.add_text(label=DocItemLabel.TEXT, text="Please confirm receipt of this order. Reference the PO number on all invoices.")
    return doc


def test_docling_document_mapping():
    parsed = docling_document_to_parsed(acme_like_docling_document())
    assert parsed.parser == "docling"
    assert parsed.text_blocks[0] == "PURCHASE ORDER"
    assert len(parsed.tables) == 4
    assert parsed.tables[2].rows[0][:3] == ["Line", "Part Number", "Description"]
    assert parsed.tables[2].page_no == 1
    assert "| Part Number" in parsed.markdown  # Markdown export preserved for audit


def test_extraction_from_docling_style_output_matches_golden(po_extractor, sample_dir):
    parsed = docling_document_to_parsed(acme_like_docling_document())
    result = po_extractor.extract(parsed)
    expected = load_expected(sample_dir / "expected" / "PO-100245.json")
    report = evaluate("acme-docling-style", expected["purchase_order"], result.purchase_order)
    assert report.mismatches == []


def test_multi_page_table_split_into_two_docling_tables(po_extractor):
    doc = DoclingDocument(name="split")
    doc.add_text(label=DocItemLabel.TEXT, text="Order No. 55-ABC")
    header = ["Pos.", "Item Code", "Item Description", "Quantity", "Unit", "Price", "Amount"]
    page1 = [header] + [[str(i), f"P-{i}", f"Widget {i}", "2", "PCS", "1.00", "2.00"] for i in range(1, 21)]
    page2 = [[str(i), f"P-{i}", f"Widget {i}", "2", "PCS", "1.00", "2.00"] for i in range(21, 26)]  # no header
    _table(doc, page1, page_no=1)
    _table(doc, page2, page_no=2)
    result = po_extractor.extract(docling_document_to_parsed(doc))
    items = result.purchase_order.line_items
    assert [i.line_number for i in items] == list(range(1, 26))
    assert items[-1].part_number == "P-25"


@run_docling
@pytest.mark.parametrize("name", ["PO-100245", "GX-PO-2026-0042", "PO-7781"])
def test_real_docling_conversion_matches_golden(name, sample_dir, po_extractor):
    from app.extraction.docling_parser import DoclingParser

    expected = load_expected(sample_dir / "expected" / f"{name}.json")
    parsed, raw = DoclingParser().parse(sample_dir.parent / expected["source_pdf"])
    assert raw and "texts" in raw
    assert parsed.tables, "Docling should detect the line-item table"
    report = evaluate(name, expected["purchase_order"], po_extractor.extract(parsed).purchase_order)
    assert report.accuracy >= 0.95, [(m.field, m.expected, m.actual) for m in report.mismatches]


@run_docling
def test_real_docling_rejects_malformed_pdf(tmp_path):
    from app.extraction.base import DocumentParseError
    from app.extraction.docling_parser import DoclingParser

    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nnot really a pdf")
    with pytest.raises(DocumentParseError):
        DoclingParser().parse(bad)
