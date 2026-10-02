from datetime import date
from decimal import Decimal

from app.extraction.validation import validate_purchase_order
from app.models.schemas import PurchaseOrder, PurchaseOrderLineItem as Item


def make_po(**overrides) -> PurchaseOrder:
    base = dict(
        purchase_order_number=" po-1 ",
        supplier_name="  ACME   Ltd ",
        supplier_email="Sales@ACME.com",
        order_date=date(2026, 1, 1),
        currency="usd",
        subtotal=Decimal("30.00"),
        tax=Decimal("3.00"),
        shipping=Decimal("2.00"),
        total_amount=Decimal("35.00"),
        line_items=[
            Item(line_number=1, part_number="A", quantity=Decimal("2"), unit_price=Decimal("10"), line_total=Decimal("20.00")),
            Item(line_number=2, part_name="B", quantity=Decimal("1"), unit_price=Decimal("10"), line_total=Decimal("10.00"), unit_of_measure=" ea "),
        ],
    )
    base.update(overrides)
    return PurchaseOrder(**base)


def test_valid_po_is_normalised():
    result = validate_purchase_order(make_po())
    assert result.is_valid and not result.errors and not result.warnings
    po = result.purchase_order
    assert po.purchase_order_number == "PO-1"
    assert po.currency == "USD"
    assert po.supplier_name == "ACME Ltd"
    assert po.supplier_email == "sales@acme.com"
    assert po.line_items[1].unit_of_measure == "EA"


def test_missing_po_number_and_items_are_errors():
    result = validate_purchase_order(make_po(purchase_order_number=None, line_items=[]))
    assert not result.is_valid
    assert {e.field for e in result.errors} == {"purchase_order_number", "line_items"}


def test_bad_quantities_are_errors():
    items = [Item(part_number="A", quantity=Decimal("0")), Item(part_number="B", quantity=None), Item(quantity=Decimal("1"))]
    result = validate_purchase_order(make_po(line_items=items, subtotal=None, total_amount=None))
    fields = {e.field for e in result.errors}
    assert fields == {"line_items[1].quantity", "line_items[2].quantity", "line_items[3]"}


def test_arithmetic_mismatches_are_warnings_not_errors():
    items = [Item(line_number=1, part_number="A", quantity=Decimal("2"), unit_price=Decimal("10"), line_total=Decimal("25.00"))]
    result = validate_purchase_order(make_po(line_items=items, subtotal=Decimal("30.00"), total_amount=Decimal("99.00")))
    assert result.is_valid
    fields = {w.field for w in result.warnings}
    assert {"line_items[1].line_total", "subtotal", "total_amount"} <= fields


def test_missing_optional_fields_only_warn():
    result = validate_purchase_order(make_po(currency=None, supplier_name=None, order_date=None, total_amount=None, subtotal=None))
    assert result.is_valid
    assert {w.field for w in result.warnings} >= {"currency", "supplier_name", "order_date", "total_amount"}
