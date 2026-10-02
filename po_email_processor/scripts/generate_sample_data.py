"""Generate the demo sample data: PO PDFs (several supplier layouts) and .eml emails.

Usage:
    python scripts/generate_sample_data.py

Outputs (committed to the repo so the demo works without running this):
    sample_data/pdfs/*.pdf
    sample_data/emails/*.eml

The PO content defined here is also the source of truth for the golden
expected-output JSON files in sample_data/expected/.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timezone
from pathlib import Path

from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

rl_config.invariant = 1  # reproducible PDF bytes -> stable SHA-256 hashes

ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = ROOT / "sample_data" / "pdfs"
EML_DIR = ROOT / "sample_data" / "emails"
EXPECTED_DIR = ROOT / "sample_data" / "expected"

styles = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=18)
BODY = ParagraphStyle("Body", parent=styles["BodyText"], fontSize=9.5, leading=12)
GRID = TableStyle(
    [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
)


def money(value: Decimal, symbol: str = "") -> str:
    return f"{symbol}{value:,.2f}"


def p(text: str) -> Paragraph:
    return Paragraph(text, BODY)


# ---------------------------------------------------------------------------
# Template 1 – "Acme Manufacturing" classic US layout, 5 line items, USD
# ---------------------------------------------------------------------------
ACME_ITEMS = [
    (1, "ABC-123", "Bearing Assembly", 100, "EA", Decimal("25.50")),
    (2, "ABC-456", "Hydraulic Pump Seal Kit", 40, "EA", Decimal("88.75")),
    (3, "XYZ-789", "Stainless Steel Bolt M8x40", 2000, "EA", Decimal("0.42")),
    (4, "GSK-220", "Gasket Sheet 1mm Neoprene", 25, "M", Decimal("31.20")),
    (5, "MTR-010", "Servo Motor 750W", 6, "EA", Decimal("1155.00")),
]


def build_acme(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=LETTER, leftMargin=18 * mm, rightMargin=18 * mm)
    subtotal = sum(q * up for _, _, _, q, _, up in ACME_ITEMS)
    tax = (subtotal * Decimal("0.0725")).quantize(Decimal("0.01"))
    shipping = Decimal("350.00")
    total = subtotal + tax + shipping

    story = [
        Paragraph("PURCHASE ORDER", H1),
        p("<b>Acme Manufacturing Inc.</b><br/>1200 Industrial Parkway<br/>Columbus, OH 43215, USA"),
        Spacer(1, 6),
        Table(
            [
                ["Purchase Order Number:", "PO-100245"],
                ["Order Date:", "03/15/2026"],
                ["Requested Delivery Date:", "04/10/2026"],
                ["Currency:", "USD"],
                ["Payment Terms:", "Net 30"],
                ["Incoterms:", "FOB Destination"],
            ],
            colWidths=[55 * mm, 60 * mm],
        ),
        Spacer(1, 8),
        Table(
            [
                [p("<b>Supplier:</b>"), p("<b>Ship To:</b>"), p("<b>Bill To:</b>")],
                [
                    p("ABC Components Ltd.<br/>45 Harbor Road<br/>Cleveland, OH 44114, USA<br/>orders@abccomponents.com"),
                    p("Acme Manufacturing - Plant 2<br/>88 Factory Lane<br/>Dayton, OH 45402, USA"),
                    p("Acme Manufacturing Inc.<br/>Accounts Payable<br/>1200 Industrial Parkway<br/>Columbus, OH 43215, USA"),
                ],
            ],
            colWidths=[58 * mm, 58 * mm, 58 * mm],
            style=GRID,
        ),
        Spacer(1, 10),
    ]
    rows = [["Line", "Part Number", "Description", "Qty", "UOM", "Unit Price", "Line Total"]]
    for line, part, desc, qty, uom, up in ACME_ITEMS:
        rows.append([str(line), part, desc, f"{qty:,}", uom, money(up, "$"), money(qty * up, "$")])
    items = Table(rows, colWidths=[12 * mm, 24 * mm, 58 * mm, 16 * mm, 14 * mm, 24 * mm, 26 * mm], repeatRows=1)
    items.setStyle(GRID)
    story += [items, Spacer(1, 8)]
    story.append(
        Table(
            [
                ["Subtotal:", money(subtotal, "$")],
                ["Sales Tax (7.25%):", money(tax, "$")],
                ["Shipping:", money(shipping, "$")],
                ["Total Amount:", money(total, "$")],
            ],
            colWidths=[40 * mm, 30 * mm],
            hAlign="RIGHT",
        )
    )
    story += [Spacer(1, 10), p("Please confirm receipt of this order. Reference the PO number on all invoices.")]
    doc.build(story)


# ---------------------------------------------------------------------------
# Template 2 – "Globex GmbH" European layout, different labels, EUR,
#              textual dates, 28 line items spanning multiple pages.
# ---------------------------------------------------------------------------
def globex_items() -> list[tuple]:
    items = []
    for i in range(1, 29):
        qty = Decimal(5 * i)
        price = Decimal("12.40") + Decimal(i) * Decimal("1.15")
        items.append((i * 10, f"GX-{4000 + i}", f"Precision Spacer Type {chr(64 + (i % 26) + 1)}{i}", qty, "PCS", price))
    return items


def build_globex(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm)
    items = globex_items()
    net = sum(q * up for *_, q, _, up in items)
    vat = (net * Decimal("0.19")).quantize(Decimal("0.01"))
    freight = Decimal("125.00")
    gross = net + vat + freight

    story = [
        Paragraph("Order / Bestellung", H1),
        p("<b>Customer:</b> Globex GmbH, Industriestrasse 14, 70565 Stuttgart, Germany"),
        Spacer(1, 6),
        Table(
            [
                ["Order No.", "GX-PO-2026-0042"],
                ["Order Date", "15 Jan 2026"],
                ["Delivery Date", "28 Feb 2026"],
                ["Currency", "EUR"],
                ["Terms of Payment", "30 days 2% discount, 60 days net"],
                ["Delivery Terms", "DAP Stuttgart (Incoterms 2020)"],
            ],
            colWidths=[45 * mm, 80 * mm],
        ),
        Spacer(1, 8),
        p("<b>Vendor:</b> Precision Parts S.r.l., Via Roma 22, 20121 Milano, Italy"),
        p("<b>Vendor Email:</b> sales@precisionparts.it"),
        p("<b>Deliver To:</b> Globex GmbH Warehouse 3, Hafenweg 9, 70565 Stuttgart, Germany"),
        p("<b>Invoice To:</b> Globex GmbH, Finance Dept., Industriestrasse 14, 70565 Stuttgart, Germany"),
        Spacer(1, 10),
    ]
    rows = [["Pos.", "Item Code", "Item Description", "Quantity", "Unit", "Price", "Amount"]]
    for pos, code, desc, qty, unit, price in items:
        rows.append([str(pos), code, desc, f"{qty}", unit, f"{price:.2f}", f"{qty * price:,.2f}"])
    table = Table(rows, colWidths=[12 * mm, 24 * mm, 62 * mm, 18 * mm, 12 * mm, 20 * mm, 26 * mm], repeatRows=1)
    table.setStyle(GRID)
    story += [table, Spacer(1, 8)]
    story.append(
        Table(
            [
                ["Net Amount", money(net, "€ ")],
                ["VAT 19%", money(vat, "€ ")],
                ["Freight", money(freight, "€ ")],
                ["Grand Total", money(gross, "€ ")],
            ],
            colWidths=[40 * mm, 34 * mm],
            hAlign="RIGHT",
        )
    )
    doc.build(story)


# ---------------------------------------------------------------------------
# Template 3 – minimal PO with missing optional fields (no tax, no terms,
#              no delivery date, no currency, no addresses for bill-to).
# ---------------------------------------------------------------------------
def build_minimal(path: Path) -> None:
    doc = SimpleDocTemplate(str(path), pagesize=LETTER)
    story = [
        Paragraph("Purchase Order", H1),
        p("<b>PO #:</b> 7781"),
        p("<b>Date:</b> 2026-02-02"),
        p("<b>Supplier:</b> Northwind Traders"),
        Spacer(1, 10),
    ]
    rows = [["Item", "Description", "Quantity", "Price", "Total"]]
    rows.append(["NW-1", "Cardboard Boxes (Large)", "500", "1.10", "550.00"])
    rows.append(["NW-2", "Packing Tape", "60", "2.25", "135.00"])
    table = Table(rows)
    table.setStyle(GRID)
    story += [table, Spacer(1, 8), p("<b>Total:</b> 685.00")]
    doc.build(story)


# ---------------------------------------------------------------------------
# Emails
# ---------------------------------------------------------------------------
def build_email(
    *,
    path: Path,
    sender: str,
    to: str,
    subject: str,
    body: str,
    attachment: Path | None,
    received: datetime,
    message_id: str,
    cc: str | None = None,
    attachment_bytes: bytes | None = None,
    attachment_name: str | None = None,
) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    if cc:
        msg["Cc"] = cc
    msg["Subject"] = subject
    msg["Date"] = format_datetime(received)
    msg["Message-ID"] = message_id
    msg.set_content(body)
    if attachment is not None:
        msg.add_attachment(attachment.read_bytes(), maintype="application", subtype="pdf", filename=attachment.name)
    elif attachment_bytes is not None:
        msg.add_attachment(attachment_bytes, maintype="application", subtype="pdf", filename=attachment_name)
    path.write_bytes(bytes(msg))


# ---------------------------------------------------------------------------
# Golden expected outputs (derived from the same source data as the PDFs,
# never from extractor output). Fields absent from a document are null.
# ---------------------------------------------------------------------------
def _d(value: Decimal | int | None) -> str | None:
    return None if value is None else str(value)


def _expected(header: dict, items: list[dict]) -> dict:
    keys = [
        "purchase_order_number", "supplier_name", "supplier_email", "supplier_address", "buyer_name",
        "buyer_address", "order_date", "requested_delivery_date", "currency", "subtotal", "tax", "shipping",
        "total_amount", "payment_terms", "shipping_terms", "ship_to_address", "bill_to_address",
    ]
    return {"purchase_order": {**{k: header.get(k) for k in keys}, "line_items": items}}


def _item(line, part, name, qty, uom, price, total) -> dict:
    return {
        "line_number": line, "part_number": part, "part_name": name, "quantity": _d(qty),
        "unit_of_measure": uom, "unit_price": _d(price), "line_total": _d(total), "requested_delivery_date": None,
    }


def write_expected() -> None:
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)
    subtotal = sum(q * up for _, _, _, q, _, up in ACME_ITEMS)
    tax = (subtotal * Decimal("0.0725")).quantize(Decimal("0.01"))
    acme = _expected(
        {
            "purchase_order_number": "PO-100245", "supplier_name": "ABC Components Ltd.",
            "supplier_email": "orders@abccomponents.com", "supplier_address": "45 Harbor Road, Cleveland, OH 44114, USA",
            "buyer_name": "Acme Manufacturing Inc.", "buyer_address": "1200 Industrial Parkway, Columbus, OH 43215, USA",
            "order_date": "2026-03-15", "requested_delivery_date": "2026-04-10", "currency": "USD",
            "subtotal": _d(subtotal), "tax": _d(tax), "shipping": "350.00", "total_amount": _d(subtotal + tax + Decimal("350.00")),
            "payment_terms": "Net 30", "shipping_terms": "FOB Destination",
            "ship_to_address": "Acme Manufacturing - Plant 2, 88 Factory Lane, Dayton, OH 45402, USA",
            "bill_to_address": "Acme Manufacturing Inc., Accounts Payable, 1200 Industrial Parkway, Columbus, OH 43215, USA",
        },
        [_item(l, p, n, q, u, up, q * up) for l, p, n, q, u, up in ACME_ITEMS],
    )
    items = globex_items()
    net = sum(q * up for *_, q, _, up in items)
    vat = (net * Decimal("0.19")).quantize(Decimal("0.01"))
    globex = _expected(
        {
            "purchase_order_number": "GX-PO-2026-0042", "supplier_name": "Precision Parts S.r.l.",
            "supplier_email": "sales@precisionparts.it", "supplier_address": "Via Roma 22, 20121 Milano, Italy",
            "buyer_name": "Globex GmbH", "buyer_address": "Industriestrasse 14, 70565 Stuttgart, Germany",
            "order_date": "2026-01-15", "requested_delivery_date": "2026-02-28", "currency": "EUR",
            "subtotal": _d(net), "tax": _d(vat), "shipping": "125.00", "total_amount": _d(net + vat + Decimal("125.00")),
            "payment_terms": "30 days 2% discount, 60 days net", "shipping_terms": "DAP Stuttgart (Incoterms 2020)",
            "ship_to_address": "Globex GmbH Warehouse 3, Hafenweg 9, 70565 Stuttgart, Germany",
            "bill_to_address": "Globex GmbH, Finance Dept., Industriestrasse 14, 70565 Stuttgart, Germany",
        },
        [_item(pos, code, desc, q, unit, price, q * price) for pos, code, desc, q, unit, price in items],
    )
    northwind = _expected(
        {
            "purchase_order_number": "7781", "supplier_name": "Northwind Traders", "order_date": "2026-02-02",
            "total_amount": "685.00",
        },
        [
            _item(1, "NW-1", "Cardboard Boxes (Large)", 500, None, Decimal("1.10"), Decimal("550.00")),
            _item(2, "NW-2", "Packing Tape", 60, None, Decimal("2.25"), Decimal("135.00")),
        ],
    )
    for name, payload, pdf in (
        ("PO-100245", acme, "acme_PO-100245.pdf"),
        ("GX-PO-2026-0042", globex, "globex_GX-PO-2026-0042.pdf"),
        ("PO-7781", northwind, "northwind_PO-7781.pdf"),
    ):
        payload = {"source_pdf": f"sample_data/pdfs/{pdf}", "document_type": "PURCHASE_ORDER", **payload}
        (EXPECTED_DIR / f"{name}.json").write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    EML_DIR.mkdir(parents=True, exist_ok=True)

    acme = PDF_DIR / "acme_PO-100245.pdf"
    globex = PDF_DIR / "globex_GX-PO-2026-0042.pdf"
    minimal = PDF_DIR / "northwind_PO-7781.pdf"
    build_acme(acme)
    build_globex(globex)
    build_minimal(minimal)
    write_expected()

    build_email(
        path=EML_DIR / "01_acme_po.eml",
        sender='"Jane Buyer" <jane.buyer@acme-mfg.example.com>',
        to="po-inbox@abccomponents.example.com",
        cc="procurement@acme-mfg.example.com, ap@acme-mfg.example.com",
        subject="Purchase Order PO-100245",
        body="Hello ABC team,\n\nPlease find attached Purchase Order PO-100245.\n"
        "Kindly confirm delivery by 10 April.\n\nRegards,\nJane Buyer\nAcme Manufacturing",
        attachment=acme,
        received=datetime(2026, 3, 15, 14, 32, 10, tzinfo=timezone.utc),
        message_id="<po-100245.acme@mail.acme-mfg.example.com>",
    )
    build_email(
        path=EML_DIR / "02_globex_po_multipage.eml",
        sender='"Klaus Einkauf" <einkauf@globex.example.de>',
        to='"Precision Parts Sales" <sales@precisionparts.example.it>',
        subject="Bestellung / Order GX-PO-2026-0042",
        body="Dear Sir or Madam,\n\nattached our order GX-PO-2026-0042 (28 positions).\n\nBest regards\nGlobex Purchasing",
        attachment=globex,
        received=datetime(2026, 1, 15, 8, 5, 0, tzinfo=timezone.utc),
        message_id="<gx-po-2026-0042@globex.example.de>",
    )
    build_email(
        path=EML_DIR / "03_northwind_minimal_po.eml",
        sender="purchasing@northwind.example.com",
        to="orders@example.com",
        subject="PO 7781",
        body="PO attached.",
        attachment=minimal,
        received=datetime(2026, 2, 2, 17, 45, 0, tzinfo=timezone.utc),
        message_id="<po-7781@northwind.example.com>",
    )
    # Same PDF as email 01, re-sent in a *different* email -> duplicate attachment.
    build_email(
        path=EML_DIR / "04_acme_po_resent.eml",
        sender='"Jane Buyer" <jane.buyer@acme-mfg.example.com>',
        to="po-inbox@abccomponents.example.com",
        subject="FW: Purchase Order PO-100245 (resend)",
        body="Resending in case the first email did not arrive.\n\nJane",
        attachment=acme,
        received=datetime(2026, 3, 16, 9, 0, 0, tzinfo=timezone.utc),
        message_id="<po-100245.resend@mail.acme-mfg.example.com>",
    )
    # Corrupt "PDF" -> permanent document parse failure.
    build_email(
        path=EML_DIR / "05_malformed_pdf.eml",
        sender="noreply@broken-supplier.example.com",
        to="po-inbox@abccomponents.example.com",
        subject="Purchase Order 999",
        body="See attached order.",
        attachment=None,
        attachment_bytes=b"%PDF-1.4\n%this is not really a pdf\n" + bytes(range(256)) * 4,
        attachment_name="PO-999.pdf",
        received=datetime(2026, 3, 20, 10, 0, 0, tzinfo=timezone.utc),
        message_id="<po-999@broken-supplier.example.com>",
    )
    # Email with no attachment -> IGNORED.
    build_email(
        path=EML_DIR / "06_no_attachment.eml",
        sender="someone@example.com",
        to="po-inbox@abccomponents.example.com",
        subject="Question about last order",
        body="Hi, when will our last order ship?",
        attachment=None,
        received=datetime(2026, 3, 21, 11, 0, 0, tzinfo=timezone.utc),
        message_id="<no-attachment-question@example.com>",
    )
    print(f"Wrote PDFs to {PDF_DIR}, emails to {EML_DIR}, golden JSON to {EXPECTED_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
