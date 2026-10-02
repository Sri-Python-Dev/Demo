"""Pure normalisation helpers: money, quantities, dates, emails, whitespace.

Every function returns ``None`` when a value cannot be interpreted with
confidence. Nothing here guesses or fabricates data.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_WS_RE = re.compile(r"[ \t ]+")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

CURRENCY_CODES = {
    "USD", "EUR", "GBP", "INR", "CAD", "AUD", "JPY", "CNY", "CHF", "MXN",
    "SGD", "HKD", "SEK", "NOK", "DKK", "NZD", "ZAR", "BRL", "AED", "KRW",
}
# Only unambiguous symbols are mapped. "$" is deliberately NOT mapped (USD/CAD/AUD/...).
CURRENCY_SYMBOLS = {"€": "EUR", "£": "GBP", "₹": "INR", "¥": None}

_MONTHS = {
    m: i + 1
    for i, names in enumerate(
        [
            ("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
            ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
            ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
            ("dec", "december"),
        ]
    )
    for m in names
}


def clean_text(value: str | None) -> str | None:
    """Collapse runs of whitespace on each line and drop empty lines."""
    if value is None:
        return None
    lines = [_WS_RE.sub(" ", line).strip() for line in str(value).replace("\r", "").split("\n")]
    text = "\n".join(line for line in lines if line)
    return text or None


def clean_inline(value: str | None) -> str | None:
    """Collapse all whitespace (including newlines) into single spaces."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def normalize_email(value: str | None) -> str | None:
    if not value:
        return None
    match = _EMAIL_RE.search(value)
    return match.group(0).lower() if match else None


def find_emails(text: str | None) -> list[str]:
    if not text:
        return []
    seen: list[str] = []
    for m in _EMAIL_RE.findall(text):
        m = m.lower()
        if m not in seen:
            seen.append(m)
    return seen


def parse_decimal(value: str | int | float | Decimal | None) -> Decimal | None:
    """Parse a monetary/quantity string such as ``$1,234.50``, ``1.234,50 €`` or ``(12.00)``."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))

    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")") or text.startswith("-") or text.endswith("-")
    # Keep digits and separators only.
    text = re.sub(r"[A-Za-z$€£₹¥\s()'+-]", "", text)
    if not text or not re.search(r"\d", text) or re.search(r"[^\d.,]", text):
        return None

    if "," in text and "." in text:
        # Whichever separator appears last is the decimal separator.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        # "1,234" / "12,345,678" -> thousands; "12,5" / "1234,56" -> decimal comma.
        if len(tail) == 3 and re.fullmatch(r"\d{1,3}(,\d{3})*", text):
            text = text.replace(",", "")
        else:
            text = head.replace(",", "") + "." + tail
    elif text.count(".") > 1:
        # "1.234.567" thousands separators.
        text = text.replace(".", "")

    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    return -number if negative else number


def parse_int(value: str | int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    match = re.fullmatch(r"\s*#?\s*(\d{1,6})\s*\.?\s*", str(value))
    return int(match.group(1)) if match else None


def detect_currency(text: str | None) -> str | None:
    """Return an ISO-4217 code only if one is explicitly present (or an unambiguous symbol)."""
    if not text:
        return None
    for token in re.findall(r"\b[A-Z]{3}\b", text.upper()):
        if token in CURRENCY_CODES:
            return token
    for symbol, code in CURRENCY_SYMBOLS.items():
        if code and symbol in text:
            return code
    return None


def parse_date(value: str | date | datetime | None, date_order: str = "MDY") -> date | None:
    """Parse common PO date formats into a ``date``. Returns None if unsure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean_inline(str(value))
    if not text:
        return None
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.I).replace(",", " ")
    text = re.sub(r"\s+", " ", text).strip()

    # ISO: 2026-01-15 or 2026/01/15
    m = re.search(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b", text)
    if m:
        return _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # Numeric: 01/15/2026, 15.01.2026, 1-15-26
    m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})\b", text)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        if a > 12 and b <= 12:
            return _safe_date(year, b, a)
        if b > 12 and a <= 12:
            return _safe_date(year, a, b)
        if "." in m.group(0) and date_order == "MDY":
            # Dotted dates are overwhelmingly European day-first.
            return _safe_date(year, b, a)
        return _safe_date(year, a, b) if date_order == "MDY" else _safe_date(year, b, a)

    # Textual: 15 Jan 2026 / 15-Jan-2026
    m = re.search(r"\b(\d{1,2})[ -]([A-Za-z]{3,9})\.?[ -](\d{2,4})\b", text)
    if m and m.group(2).lower() in _MONTHS:
        return _safe_date(_year(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))

    # Textual: January 15 2026 / Jan. 15 2026
    m = re.search(r"\b([A-Za-z]{3,9})\.? (\d{1,2}) (\d{4})\b", text)
    if m and m.group(1).lower() in _MONTHS:
        return _safe_date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))

    return None


def _year(text: str) -> int:
    year = int(text)
    return year + 2000 if year < 100 else year


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None
