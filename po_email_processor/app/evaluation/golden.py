"""Field-level comparison of an extracted PO against a golden expected JSON.

Seed of the golden-dataset evaluation framework: each expected file in
``sample_data/expected`` describes the correct extraction for one PDF. The
same comparison is used by pytest and by ``scripts/evaluate_golden.py``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.models.schemas import PurchaseOrder

NUMERIC = {"subtotal", "tax", "shipping", "total_amount", "quantity", "unit_price", "line_total"}
DATES = {"order_date", "requested_delivery_date"}
EXACT = {"purchase_order_number", "currency", "supplier_email", "unit_of_measure", "part_number", "line_number"}
# Everything else is free text: compared ignoring case, whitespace and punctuation,
# because parsers differ in how they join multi-line cells (", " vs " ").


@dataclass
class FieldResult:
    field: str
    expected: Any
    actual: Any
    match: bool


@dataclass
class EvaluationReport:
    name: str
    results: list[FieldResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def correct(self) -> int:
        return sum(r.match for r in self.results)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 1.0

    @property
    def mismatches(self) -> list[FieldResult]:
        return [r for r in self.results if not r.match]


def _text_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def values_match(name: str, expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None  # hallucinated values count as errors
    if name in NUMERIC:
        return Decimal(str(expected)) == Decimal(str(actual))
    if name in DATES:
        actual_iso = actual.isoformat() if isinstance(actual, date) else str(actual)
        return str(expected) == actual_iso
    if name in EXACT:
        return str(expected).strip() == str(actual).strip()
    return _text_key(expected) == _text_key(actual)


def evaluate(name: str, expected_po: dict[str, Any], actual: PurchaseOrder) -> EvaluationReport:
    report = EvaluationReport(name=name)
    for key, expected in expected_po.items():
        if key == "line_items":
            continue
        actual_value = getattr(actual, key)
        report.results.append(FieldResult(key, expected, actual_value, values_match(key, expected, actual_value)))

    expected_items = expected_po.get("line_items", [])
    report.results.append(
        FieldResult("line_items.count", len(expected_items), len(actual.line_items), len(expected_items) == len(actual.line_items))
    )
    for idx, exp_item in enumerate(expected_items):
        act_item = actual.line_items[idx] if idx < len(actual.line_items) else None
        for key, expected in exp_item.items():
            actual_value = getattr(act_item, key) if act_item else None
            report.results.append(
                FieldResult(f"line_items[{idx + 1}].{key}", expected, actual_value, values_match(key, expected, actual_value))
            )
    return report


def load_expected(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())
