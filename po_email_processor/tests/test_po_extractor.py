from decimal import Decimal

import pytest

from app.evaluation.golden import evaluate, load_expected
from app.extraction.base import DocumentParseError
from app.models.schemas import ParsedDocument, ParsedTable

GOLDEN = ["PO-100245", "GX-PO-2026-0042", "PO-7781"]


def parsed(text_blocks=None, tables=None) -> ParsedDocument:
    return ParsedDocument(
        parser="test", markdown="", text_blocks=text_blocks or [],
        tables=[ParsedTable(rows=t) for t in (tables or [])],
    )


@pytest.mark.parametrize("name", GOLDEN)
def test_sample_pdfs_match_golden_expected_json(name, sample_dir, pdfplumber_parser, po_extractor):
    expected = load_expected(sample_dir / "expected" / f"{name}.json")
    doc, _ = pdfplumber_parser.parse(sample_dir.parent / expected["source_pdf"])
    result = po_extractor.extract(doc)
    report = evaluate(name, expected["purchase_order"], result.purchase_order)
    assert report.mismatches == [], [(m.field, m.expected, m.actual) for m in report.mismatches]


def test_output_is_valid_schema_json(sample_dir, pdfplumber_parser, po_extractor):
    from app.models.schemas import ExtractionResult

    doc, _ = pdfplumber_parser.parse(sample_dir / "pdfs" / "acme_PO-100245.pdf")
    result = po_extractor.extract(doc)
    round_tripped = ExtractionResult.model_validate_json(result.model_dump_json())
    assert round_tripped == result
    assert round_tripped.purchase_order.total_amount == Decimal("16062.12")
    assert result.field_evidence["purchase_order_number"].startswith("Purchase Order Number")


def test_multi_page_po_with_28_line_items(sample_dir, pdfplumber_parser, po_extractor):
    doc, _ = pdfplumber_parser.parse(sample_dir / "pdfs" / "globex_GX-PO-2026-0042.pdf")
    assert doc.page_count == 2
    items = po_extractor.extract(doc).purchase_order.line_items
    assert len(items) == 28
    assert [i.line_number for i in items] == list(range(10, 290, 10))
    assert sum(i.line_total for i in items) == Decimal("69527.50")


def test_missing_fields_stay_null(sample_dir, pdfplumber_parser, po_extractor):
    doc, _ = pdfplumber_parser.parse(sample_dir / "pdfs" / "northwind_PO-7781.pdf")
    result = po_extractor.extract(doc)
    po = result.purchase_order
    for name in ("currency", "subtotal", "tax", "shipping", "payment_terms", "shipping_terms",
                 "ship_to_address", "bill_to_address", "buyer_name", "requested_delivery_date"):
        assert getattr(po, name) is None, name
    assert all(i.unit_of_measure is None for i in po.line_items)
    assert "line numbers not present" in " ".join(result.warnings)


def test_alternative_labels_and_layout(po_extractor):
    doc = parsed(
        text_blocks=[
            "P.O. No: 4500012345",
            "PO Date: 2026-05-01",
            "Need By: 2026-05-20",
            "Vendor Name: Initech LLC, 4120 Freidrich Lane, Austin, TX 78744",
            "Vendor E-mail: Sales@Initech.example",
            "Freight Terms: CIF Rotterdam",
            "Terms: 2/10 Net 45",
        ],
        tables=[
            [
                ["Item", "SKU", "Item Description", "Ordered Qty", "U/M", "Rate", "Extended Price", "Delivery Date"],
                ["1", "TPS-1", "TPS Report Covers", "250", "pk", "3.10", "775.00", "2026-05-18"],
                ["2", "STP-9", "Red Stapler", "3", "ea", "21.99", "65.97", ""],
                ["", "", "(Swingline, model 747)", "", "", "", "", ""],
                ["Grand Total", "", "", "", "", "", "840.97", ""],
            ]
        ],
    )
    po = po_extractor.extract(doc).purchase_order
    assert po.purchase_order_number == "4500012345"
    assert str(po.order_date) == "2026-05-01"
    assert str(po.requested_delivery_date) == "2026-05-20"
    assert po.supplier_name == "Initech LLC"
    assert po.supplier_address == "4120 Freidrich Lane, Austin, TX 78744"
    assert po.supplier_email == "sales@initech.example"
    assert po.shipping_terms == "CIF Rotterdam"
    assert po.payment_terms == "2/10 Net 45"
    assert po.total_amount == Decimal("840.97")
    assert [i.part_number for i in po.line_items] == ["TPS-1", "STP-9"]
    assert po.line_items[0].unit_of_measure == "PK"
    assert str(po.line_items[0].requested_delivery_date) == "2026-05-18"
    assert po.line_items[1].requested_delivery_date is None
    assert po.line_items[1].part_name == "Red Stapler (Swingline, model 747)"  # wrapped row merged


def test_ambiguous_dollar_is_not_turned_into_usd(po_extractor):
    doc = parsed(
        text_blocks=["PO Number: 77", "Total: $10.00"],
        tables=[[["Description", "Qty", "Price"], ["Thing", "1", "$10.00"]]],
    )
    result = po_extractor.extract(doc)
    assert result.purchase_order.currency is None
    assert any("ambiguous" in w for w in result.warnings)


def test_document_without_po_content_returns_nulls(po_extractor):
    doc = parsed(text_blocks=["Dear customer,", "thank you for your business."])
    result = po_extractor.extract(doc)
    po = result.purchase_order
    assert po.purchase_order_number is None
    assert po.line_items == []
    assert po.model_dump(exclude={"line_items"}) == {k: None for k in po.model_dump(exclude={"line_items"})}
    assert "no line items found" in result.warnings


def test_malformed_pdf_raises_parse_error(tmp_path, pdfplumber_parser):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.4\n%garbage" + bytes(range(256)))
    with pytest.raises(DocumentParseError):
        pdfplumber_parser.parse(bad)


def test_empty_pdf_raises_parse_error(tmp_path, pdfplumber_parser):
    from reportlab.pdfgen import canvas

    blank = tmp_path / "blank.pdf"
    c = canvas.Canvas(str(blank))
    c.showPage()
    c.save()
    with pytest.raises(DocumentParseError, match="No extractable content"):
        pdfplumber_parser.parse(blank)
