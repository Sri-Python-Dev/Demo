"""Business validation and final normalisation of an extracted Purchase Order.

Errors make the document invalid (permanent failure, not retried); warnings
are recorded for review but do not block persistence.
"""

from __future__ import annotations

from decimal import Decimal

from app.extraction import normalization as norm
from app.models.schemas import PurchaseOrder, ValidationIssue, ValidationResult

MONEY_TOLERANCE = Decimal("0.02")


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(MONEY_TOLERANCE, abs(b) * Decimal("0.0005"))


def normalize_purchase_order(po: PurchaseOrder) -> PurchaseOrder:
    po = po.model_copy(deep=True)
    if po.purchase_order_number:
        po.purchase_order_number = po.purchase_order_number.strip().upper()
    if po.currency:
        po.currency = po.currency.strip().upper()
    po.supplier_email = norm.normalize_email(po.supplier_email)
    for name in (
        "supplier_name", "supplier_address", "buyer_name", "buyer_address", "payment_terms",
        "shipping_terms", "ship_to_address", "bill_to_address",
    ):
        setattr(po, name, norm.clean_inline(getattr(po, name)))
    for item in po.line_items:
        item.part_number = norm.clean_inline(item.part_number)
        item.part_name = norm.clean_inline(item.part_name)
        item.unit_of_measure = (norm.clean_inline(item.unit_of_measure) or "").upper() or None
    return po


def validate_purchase_order(po: PurchaseOrder) -> ValidationResult:
    po = normalize_purchase_order(po)
    errors: list[ValidationIssue] = []
    warnings: list[ValidationIssue] = []

    def error(field: str, message: str) -> None:
        errors.append(ValidationIssue(field=field, message=message, severity="error"))

    def warn(field: str, message: str) -> None:
        warnings.append(ValidationIssue(field=field, message=message, severity="warning"))

    # --- required -------------------------------------------------------
    if not po.purchase_order_number:
        error("purchase_order_number", "PO number is required")
    if not po.line_items:
        error("line_items", "at least one line item is required")

    # --- recommended ----------------------------------------------------
    for name in ("supplier_name", "order_date", "currency", "total_amount"):
        if getattr(po, name) in (None, ""):
            warn(name, f"{name} is missing")

    if po.order_date and po.requested_delivery_date and po.requested_delivery_date < po.order_date:
        warn("requested_delivery_date", "requested delivery date is before the order date")

    # --- line items -----------------------------------------------------
    seen_numbers: set[int] = set()
    for idx, item in enumerate(po.line_items, start=1):
        ref = f"line_items[{idx}]"
        if not item.part_number and not item.part_name:
            error(ref, "line item has neither part number nor description")
        if item.quantity is None:
            error(f"{ref}.quantity", "quantity is missing")
        elif item.quantity <= 0:
            error(f"{ref}.quantity", f"quantity must be positive (got {item.quantity})")
        if item.unit_price is not None and item.unit_price < 0:
            error(f"{ref}.unit_price", "unit price must not be negative")
        if item.line_number is not None:
            if item.line_number in seen_numbers:
                warn(f"{ref}.line_number", f"duplicate line number {item.line_number}")
            seen_numbers.add(item.line_number)
        if item.quantity is not None and item.unit_price is not None and item.line_total is not None:
            expected = item.quantity * item.unit_price
            if not _close(item.line_total, expected):
                warn(f"{ref}.line_total", f"line total {item.line_total} != quantity x unit price ({expected})")

    # --- totals ---------------------------------------------------------
    line_totals = [i.line_total for i in po.line_items if i.line_total is not None]
    if line_totals and len(line_totals) == len(po.line_items):
        lines_sum = sum(line_totals, Decimal("0"))
        if po.subtotal is not None and not _close(po.subtotal, lines_sum):
            warn("subtotal", f"subtotal {po.subtotal} != sum of line totals ({lines_sum})")
        if po.subtotal is None and po.tax is None and po.shipping is None and po.total_amount is not None:
            if not _close(po.total_amount, lines_sum):
                warn("total_amount", f"total {po.total_amount} != sum of line totals ({lines_sum})")
    if po.subtotal is not None and po.total_amount is not None:
        expected_total = po.subtotal + (po.tax or 0) + (po.shipping or 0)
        if not _close(po.total_amount, expected_total):
            warn("total_amount", f"total {po.total_amount} != subtotal + tax + shipping ({expected_total})")

    return ValidationResult(is_valid=not errors, errors=errors, warnings=warnings, purchase_order=po)
