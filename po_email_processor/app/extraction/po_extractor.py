"""Rule-based Purchase Order extractor.

Works on the parser-agnostic :class:`ParsedDocument` (text blocks + table
grids produced by Docling), not on raw PDF text:

* Header fields are found by a label vocabulary (``po_field_config``) applied
  to text lines and to table cells (label in one cell, value to the right or
  below), so it tolerates different templates and label wording.
* Line items come from whichever table has a recognisable header (quantity +
  description/part columns), including tables split across pages.
* Values are normalised (ISO dates, Decimal money/quantities, lower-case
  emails, collapsed whitespace). Anything that cannot be found stays ``None``;
  nothing is invented.

An LLM-based extractor can implement the same :class:`DocumentExtractor`
interface and be swapped in via ``app.extraction.factory`` without any change
to activities or the workflow.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.extraction import normalization as norm
from app.extraction.base import DocumentExtractor
from app.extraction.po_field_config import (
    ADDRESS_FIELDS,
    AMBIGUOUS_ITEM_HEADER,
    COMPANY_SUFFIX,
    DATE_FIELDS,
    HEADER_FIELD_LABELS,
    LINE_ITEM_COLUMNS,
    MONEY_FIELDS,
    PARTY_FIELDS,
)
from app.models.schemas import (
    DocumentType,
    ExtractionResult,
    ParsedDocument,
    ParsedTable,
    PurchaseOrder,
    PurchaseOrderLineItem,
)

logger = logging.getLogger(__name__)

_LABEL_END = r"(?=$|[\s:#])"
_SEPARATOR = re.compile(r"^\s*(?:[:#]|[-–](?=\s))?\s*")
_MD_NOISE = re.compile(r"(\*\*|__|^#+\s*|^[-*]\s+|`)")
_PERCENT = re.compile(r"\d+(?:[.,]\d+)?\s*%")
_NUMBER_TOKEN = re.compile(r"\(?-?\d[\d.,]*\d\)?|\(?-?\d\)?")
_PO_NUMBER = re.compile(r"\b([A-Z0-9][A-Z0-9\-/_.]*\d[A-Z0-9\-/_.]*)\b", re.I)
_SUFFIX_RE = re.compile(rf"^(.*?\b{COMPANY_SUFFIX})(?=[\s,;]|$)", re.I)
_TOTAL_ROW_FIELDS = MONEY_FIELDS


@dataclass
class _Label:
    field_name: str
    regex: re.Pattern


@dataclass
class _Candidate:
    value: str
    evidence: str
    following: list[str] = field(default_factory=list)  # lines after an empty-valued label
    priority: int = 0  # 0 = value on the label's line / right cell; 1 = fallback (next line / cell below)


def _compile_labels() -> list[_Label]:
    labels = []
    for field_name, patterns in HEADER_FIELD_LABELS.items():
        for pattern in patterns:
            labels.append(_Label(field_name, re.compile(rf"^\s*(?:{pattern}){_LABEL_END}", re.I)))
    return labels


_LABELS = _compile_labels()
_COLUMN_PATTERNS = {
    name: [re.compile(rf"^(?:{p})$", re.I) for p in patterns] for name, patterns in LINE_ITEM_COLUMNS.items()
}
_ITEM_RE = re.compile(rf"^(?:{AMBIGUOUS_ITEM_HEADER})$", re.I)


def _strip_markdown(text: str) -> str:
    return _MD_NOISE.sub("", text).strip()


def match_label(text: str) -> tuple[str, str] | None:
    """Return (field_name, remaining_value) for the longest label at the start of ``text``."""
    text = _strip_markdown(text)
    best: tuple[int, str] | None = None
    for label in _LABELS:
        m = label.regex.match(text)
        if m and (best is None or m.end() > best[0]):
            best = (m.end(), label.field_name)
    if best is None:
        return None
    rest = _SEPARATOR.sub("", text[best[0]:], count=1).strip()
    return best[1], rest


def _normalise_header(cell: str) -> str:
    return re.sub(r"\s+", " ", cell.replace("\n", " ")).strip().rstrip(":").strip().lower()


def last_money(value: str | None) -> Decimal | None:
    """Pick the last monetary number in a value, ignoring percentages: 'VAT 19% € 1,234.00' -> 1234.00."""
    if not value:
        return None
    tokens = _NUMBER_TOKEN.findall(_PERCENT.sub(" ", value))
    for token in reversed(tokens):
        number = norm.parse_decimal(token)
        if number is not None:
            return number
    return None


def split_party(value: str) -> tuple[str | None, str | None, str | None]:
    """Split a party block into (name, address, email)."""
    email = norm.normalize_email(value)
    text = re.sub(r"\S+@\S+", " ", value) if email else value
    lines = [ln.strip(" ,;") for ln in text.replace("\r", "").split("\n") if ln.strip(" ,;")]
    if not lines:
        return None, None, email
    if len(lines) > 1:
        return norm.clean_inline(lines[0]), norm.clean_inline(", ".join(lines[1:])), email
    single = lines[0]
    m = _SUFFIX_RE.match(single)
    if m and m.group(1).split()[-1][:1].isupper():
        name = m.group(1).strip(" ,")
        rest = single[m.end():].strip(" ,;")
        return norm.clean_inline(name), norm.clean_inline(rest) or None, email
    if "," in single:
        name, _, rest = single.partition(",")
        return norm.clean_inline(name), norm.clean_inline(rest), email
    return norm.clean_inline(single), None, email


def _looks_like_address_line(line: str) -> bool:
    """Short, contains a number or comma, and is not a sentence."""
    line = line.strip()
    return (
        len(line) <= 80
        and len(line.split()) <= 10
        and bool(re.search(r"[\d,]", line))
        and not re.search(r"[.!?]$", line)
    )


def _address(value: str) -> str | None:
    lines = [ln.strip(" ,;") for ln in value.replace("\r", "").split("\n") if ln.strip(" ,;")]
    return norm.clean_inline(", ".join(lines))


class RuleBasedPurchaseOrderExtractor(DocumentExtractor):
    name = "rule_based_po_extractor"
    version = "1.0.0"
    document_type = DocumentType.PURCHASE_ORDER

    def __init__(self, date_order: str = "MDY") -> None:
        self.date_order = date_order

    # ------------------------------------------------------------------ API
    def extract(self, parsed: ParsedDocument) -> ExtractionResult:
        warnings: list[str] = []
        evidence: dict[str, str] = {}

        line_tables, items, totals_rows, item_warnings = self._extract_line_items(parsed.tables)
        warnings.extend(item_warnings)

        candidates = self._collect_candidates(parsed, line_tables, totals_rows)
        po = PurchaseOrder(line_items=items)
        self._apply_candidates(po, candidates, evidence, warnings)

        if po.buyer_name is None:
            self._letterhead_buyer(po, parsed.text_blocks, evidence, warnings)
        if po.currency is None:
            self._fallback_currency(po, parsed, evidence, warnings)

        for name in ("purchase_order_number", "supplier_name", "order_date", "total_amount"):
            if getattr(po, name) is None:
                warnings.append(f"{name} not found in document")
        if not items:
            warnings.append("no line items found")

        logger.info(
            "Extracted PO %s: supplier=%s items=%d total=%s warnings=%d",
            po.purchase_order_number, po.supplier_name, len(items), po.total_amount, len(warnings),
        )
        return ExtractionResult(
            document_id=parsed.document_id,
            document_type=self.document_type,
            extractor=self.name,
            extractor_version=self.version,
            purchase_order=po,
            warnings=warnings,
            field_evidence=evidence,
        )

    # --------------------------------------------------------- header fields
    def _collect_candidates(
        self, parsed: ParsedDocument, line_tables: set[int], totals_rows: dict[int, list[int]]
    ) -> dict[str, list[_Candidate]]:
        found: dict[str, list[_Candidate]] = {}

        def add(field_name: str, value: str, evidence: str, following: list[str] | None = None, priority: int = 0) -> None:
            found.setdefault(field_name, []).append(_Candidate(value, evidence, following or [], priority))

        # 1) Text lines: "Label: value" (value may continue on following lines).
        lines: list[str] = []
        for block in parsed.text_blocks:
            lines.extend(ln for ln in block.split("\n") if ln.strip())
        for i, line in enumerate(lines):
            hit = match_label(line)
            if not hit:
                continue
            field_name, value = hit
            following = []
            for nxt in lines[i + 1 : i + 5]:
                if match_label(nxt):
                    break
                following.append(_strip_markdown(nxt))
            add(field_name, value, line.strip(), following)
            if following and field_name not in PARTY_FIELDS | ADDRESS_FIELDS:
                add(field_name, following[0], f"{line.strip()} / {following[0]}", priority=1)

        # 2) Tables: label cell with value inside it, in the next cell to the right, or below.
        for t_index, table in enumerate(parsed.tables):
            rows = table.rows
            allowed_rows = totals_rows.get(t_index) if t_index in line_tables else None
            for r, row in enumerate(rows):
                if allowed_rows is not None and r not in allowed_rows:
                    continue
                for c, cell in enumerate(row):
                    if not cell:
                        continue
                    hit = match_label(cell)
                    if not hit:
                        continue
                    field_name, value = hit
                    if value:
                        add(field_name, value, cell)
                    right = next((x for x in row[c + 1 :] if x.strip()), None)
                    if right is not None and not match_label(right):
                        add(field_name, right, f"{cell} | {right}")
                        continue
                    if r + 1 < len(rows) and c < len(rows[r + 1]):
                        below = rows[r + 1][c]
                        if below.strip() and not match_label(below):
                            add(field_name, below, f"{cell} / {below}", priority=1)
        return found

    def _apply_candidates(
        self,
        po: PurchaseOrder,
        candidates: dict[str, list[_Candidate]],
        evidence: dict[str, str],
        warnings: list[str],
    ) -> None:
        for field_name, options in candidates.items():
            for cand in sorted(options, key=lambda c: c.priority):  # stable: document order within a priority
                if self._apply_one(po, field_name, cand, evidence):
                    break

    def _apply_one(self, po: PurchaseOrder, field_name: str, cand: _Candidate, evidence: dict[str, str]) -> bool:
        value = cand.value
        if not value and cand.following and (field_name in PARTY_FIELDS or field_name in ADDRESS_FIELDS):
            value = "\n".join(cand.following)
        if not value:
            return False

        def set_(name: str, val) -> bool:
            if val is None or getattr(po, name) is not None:
                return False
            setattr(po, name, val)
            evidence[name] = cand.evidence
            return True

        if field_name == "purchase_order_number":
            m = _PO_NUMBER.search(value)
            return set_(field_name, m.group(1).strip(".") if m and len(m.group(1)) <= 40 else None)
        if field_name in DATE_FIELDS:
            return set_(field_name, norm.parse_date(value, self.date_order))
        if field_name in MONEY_FIELDS:
            return set_(field_name, last_money(value))
        if field_name == "currency":
            return set_(field_name, norm.detect_currency(value))
        if field_name == "supplier_email":
            return set_(field_name, norm.normalize_email(value))
        if field_name in PARTY_FIELDS:
            name, address, email = split_party(value)
            ok = set_(field_name, name)
            if ok:
                addr_field = "supplier_address" if field_name == "supplier_name" else "buyer_address"
                set_(addr_field, address)
                if field_name == "supplier_name":
                    set_("supplier_email", email)
            return ok
        if field_name in ADDRESS_FIELDS:
            return set_(field_name, _address(value))
        return set_(field_name, norm.clean_inline(value))

    def _letterhead_buyer(
        self, po: PurchaseOrder, blocks: list[str], evidence: dict[str, str], warnings: list[str]
    ) -> None:
        """POs are issued by the buyer, whose letterhead normally precedes the first labelled field."""
        lines = [ln.strip() for b in blocks for ln in b.split("\n") if ln.strip()]
        for i, line in enumerate(lines[:6]):
            hit = match_label(line)
            if hit and hit[1]:
                return  # reached labelled content; no letterhead found
            if hit:
                continue  # a bare title such as "PURCHASE ORDER"
            m = _SUFFIX_RE.match(line)
            if not m or not m.group(1).split()[-1][:1].isupper():
                continue
            name, address, _ = split_party(line)
            if address is None:
                address_lines = []
                for nxt in lines[i + 1 : i + 4]:
                    if match_label(nxt) or not _looks_like_address_line(nxt):
                        break
                    address_lines.append(nxt)
                if address_lines:
                    name, address, _ = split_party("\n".join([line, *address_lines]))
            po.buyer_name = name
            if po.buyer_address is None:
                po.buyer_address = address
            evidence["buyer_name"] = line
            warnings.append("buyer_name taken from document letterhead (no explicit buyer label)")
            return

    def _fallback_currency(
        self, po: PurchaseOrder, parsed: ParsedDocument, evidence: dict[str, str], warnings: list[str]
    ) -> None:
        corpus = "\n".join(parsed.text_blocks + [c for t in parsed.tables for r in t.rows for c in r])
        code = norm.detect_currency(corpus)
        if code:
            po.currency = code
            evidence["currency"] = "currency code/symbol found in document body"
        elif "$" in corpus:
            warnings.append("currency symbol '$' found but ambiguous (USD/CAD/AUD...); currency left empty")

    # ------------------------------------------------------------ line items
    def _map_header(self, row: list[str]) -> dict[str, int]:
        mapping: dict[str, int] = {}
        ambiguous: list[int] = []
        for idx, cell in enumerate(row):
            header = _normalise_header(cell)
            if not header:
                continue
            if _ITEM_RE.match(header):
                ambiguous.append(idx)
                continue
            for field_name, patterns in _COLUMN_PATTERNS.items():
                if field_name not in mapping and any(p.match(header) for p in patterns):
                    mapping[field_name] = idx
                    break
        for idx in ambiguous:
            mapping.setdefault("_item", idx)
        return mapping

    @staticmethod
    def _is_line_item_header(mapping: dict[str, int]) -> bool:
        has_desc = "part_name" in mapping or "part_number" in mapping or "_item" in mapping
        has_numbers = "quantity" in mapping or ("unit_price" in mapping and "line_total" in mapping)
        return has_desc and has_numbers and len(mapping) >= 3

    def _resolve_item_column(self, mapping: dict[str, int], rows: list[list[str]]) -> dict[str, int]:
        if "_item" not in mapping:
            return mapping
        idx = mapping.pop("_item")
        values = [r[idx] for r in rows if idx < len(r) and r[idx].strip()]
        all_ints = values and all(norm.parse_int(v) is not None for v in values)
        if all_ints and "line_number" not in mapping:
            mapping["line_number"] = idx
        elif "part_number" not in mapping:
            mapping["part_number"] = idx
        elif "part_name" not in mapping:
            mapping["part_name"] = idx
        return mapping

    def _extract_line_items(
        self, tables: list[ParsedTable]
    ) -> tuple[set[int], list[PurchaseOrderLineItem], dict[int, list[int]], list[str]]:
        items: list[PurchaseOrderLineItem] = []
        warnings: list[str] = []
        line_tables: set[int] = set()
        totals_rows: dict[int, list[int]] = {}
        current: dict[str, int] | None = None
        current_width: int | None = None
        last_was_items = False

        for t_index, table in enumerate(tables):
            rows = table.rows
            header_row = None
            mapping: dict[str, int] = {}
            for r in range(min(3, len(rows))):
                candidate = self._map_header(rows[r])
                if self._is_line_item_header(candidate):
                    header_row, mapping = r, candidate
                    break

            if header_row is None:
                width = max((len(r) for r in rows), default=0)
                # Continuation of a table split across pages (no repeated header).
                if last_was_items and current is not None and width == current_width and not self._looks_like_kv(rows):
                    header_row, mapping = -1, dict(current)
                else:
                    last_was_items = False
                    continue

            data_rows = rows[header_row + 1 :]
            mapping = self._resolve_item_column(mapping, data_rows)
            current, current_width, last_was_items = mapping, max(len(r) for r in rows), True
            line_tables.add(t_index)
            header_signature = [_normalise_header(c) for c in rows[header_row]] if header_row >= 0 else None

            for offset, row in enumerate(data_rows):
                r_index = header_row + 1 + offset
                if header_signature and [_normalise_header(c) for c in row] == header_signature:
                    continue  # header repeated mid-table
                if any(match_label(c) and match_label(c)[0] in _TOTAL_ROW_FIELDS for c in row if c):
                    totals_rows.setdefault(t_index, []).append(r_index)
                    continue
                item = self._parse_row(row, mapping)
                if item is None:
                    continue
                if (
                    items
                    and item.quantity is None
                    and item.unit_price is None
                    and item.line_total is None
                    and item.part_number is None
                    and item.part_name
                ):
                    # Wrapped description continuing the previous line.
                    prev = items[-1]
                    prev.part_name = norm.clean_inline(f"{prev.part_name or ''} {item.part_name}")
                    continue
                items.append(item)

        if items and all(i.line_number is None for i in items):
            for n, item in enumerate(items, start=1):
                item.line_number = n
            warnings.append("line numbers not present in document; assigned sequentially by row order")
        return line_tables, items, totals_rows, warnings

    @staticmethod
    def _looks_like_kv(rows: list[list[str]]) -> bool:
        return sum(1 for r in rows for c in r if c and match_label(c)) >= max(1, len(rows) // 2)

    def _parse_row(self, row: list[str], mapping: dict[str, int]) -> PurchaseOrderLineItem | None:
        def cell(name: str) -> str | None:
            idx = mapping.get(name)
            if idx is None or idx >= len(row):
                return None
            return norm.clean_inline(row[idx])

        quantity_raw = cell("quantity")
        item = PurchaseOrderLineItem(
            line_number=norm.parse_int(cell("line_number")),
            part_number=cell("part_number"),
            part_name=cell("part_name"),
            quantity=norm.parse_decimal(quantity_raw),
            unit_of_measure=(cell("unit_of_measure") or "").upper() or None,
            unit_price=norm.parse_decimal(cell("unit_price")),
            line_total=norm.parse_decimal(cell("line_total")),
            requested_delivery_date=norm.parse_date(cell("requested_delivery_date"), self.date_order),
        )
        if item.unit_of_measure is None and quantity_raw:
            # "100 EA" -> UOM is literally in the document.
            m = re.fullmatch(r"[\d.,]+\s*([A-Za-z]{1,5})", quantity_raw)
            if m:
                item.unit_of_measure = m.group(1).upper()
        if not any([item.part_number, item.part_name, item.quantity, item.unit_price, item.line_total]):
            return None
        return item
