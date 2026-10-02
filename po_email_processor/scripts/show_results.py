"""Print what the pipeline stored in PostgreSQL (no psql needed).

    python scripts/show_results.py              # summary of emails, documents, POs
    python scripts/show_results.py PO-100245    # one PO with all line items and its JSON
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import selectinload  # noqa: E402

from app.database.connection import get_engine, make_session_factory  # noqa: E402
from app.models.database import Document, InboundEmail, PurchaseOrderRecord  # noqa: E402


def table(headers: list[str], rows: list[list]) -> None:
    cells = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
    widths = [min(w, 48) for w in widths]
    fmt = "  ".join(f"{{:<{w}.{w}}}" for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for r in cells:
        print(fmt.format(*r))
    print()


def main() -> None:
    session = make_session_factory(get_engine())()
    if len(sys.argv) > 1:
        po = session.scalars(
            select(PurchaseOrderRecord).options(selectinload(PurchaseOrderRecord.items))
            .where(PurchaseOrderRecord.po_number == sys.argv[1])
        ).first()
        if po is None:
            sys.exit(f"PO {sys.argv[1]} not found")
        header = {c.name: getattr(po, c.key) for c in PurchaseOrderRecord.__table__.columns if c.name != "dedupe_key"}
        print(json.dumps(header, indent=2, default=str))
        table(
            ["#", "part_number", "part_name", "qty", "uom", "unit_price", "line_total"],
            [[i.line_number, i.part_number, i.part_name, i.quantity, i.unit_of_measure, i.unit_price, i.line_total] for i in po.items],
        )
        return

    print("=== inbound_emails")
    table(
        ["received_at", "sender", "subject", "status", "error"],
        [[e.received_at, e.sender_email, e.subject, e.processing_status, e.error_message]
         for e in session.scalars(select(InboundEmail).order_by(InboundEmail.received_at))],
    )
    print("=== documents")
    table(
        ["filename", "sha256", "parser", "pages", "status", "error"],
        [[d.filename, d.file_hash[:12] + "...", d.parser, d.page_count, d.processing_status, d.error_message]
         for d in session.scalars(select(Document).order_by(Document.created_at))],
    )
    print("=== purchase_orders")
    table(
        ["po_number", "supplier", "buyer", "order_date", "currency", "total", "items"],
        [[p.po_number, p.supplier_name, p.buyer_name, p.order_date, p.currency, p.total_amount, len(p.items)]
         for p in session.scalars(select(PurchaseOrderRecord).options(selectinload(PurchaseOrderRecord.items)).order_by(PurchaseOrderRecord.order_date))],
    )


if __name__ == "__main__":
    main()
