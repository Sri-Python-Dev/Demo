from datetime import date
from decimal import Decimal

import pytest

from app.extraction import normalization as n


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("$1,234.50", Decimal("1234.50")),
        ("1.234,50 €", Decimal("1234.50")),
        ("€ 69,527.50", Decimal("69527.50")),
        ("2,000", Decimal("2000")),
        ("12,5", Decimal("12.5")),
        ("(12.00)", Decimal("-12.00")),
        ("USD 99", Decimal("99")),
        ("1.234.567", Decimal("1234567")),
        ("", None),
        ("N/A", None),
        (None, None),
    ],
)
def test_parse_decimal(raw, expected):
    assert n.parse_decimal(raw) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-03-15", date(2026, 3, 15)),
        ("03/15/2026", date(2026, 3, 15)),
        ("15/03/2026", date(2026, 3, 15)),  # unambiguous day-first
        ("15.03.2026", date(2026, 3, 15)),
        ("15 Jan 2026", date(2026, 1, 15)),
        ("January 15th, 2026", date(2026, 1, 15)),
        ("28-Feb-26", date(2026, 2, 28)),
        ("31/02/2026", None),  # impossible date -> None, never guessed
        ("next Tuesday", None),
        (None, None),
    ],
)
def test_parse_date(raw, expected):
    assert n.parse_date(raw) == expected


def test_ambiguous_numeric_date_follows_configured_order():
    assert n.parse_date("03/04/2026", "MDY") == date(2026, 3, 4)
    assert n.parse_date("03/04/2026", "DMY") == date(2026, 4, 3)


def test_email_and_whitespace():
    assert n.normalize_email("  Jane <JANE.Buyer@Acme.COM> ") == "jane.buyer@acme.com"
    assert n.normalize_email("no email here") is None
    assert n.clean_inline("  a \n  b\t c ") == "a b c"
    assert n.clean_text("a  \n\n  b ") == "a\nb"


def test_currency_detection_never_guesses_dollar():
    assert n.detect_currency("Currency: usd") == "USD"
    assert n.detect_currency("Total € 10") == "EUR"
    assert n.detect_currency("Total $10") is None  # USD? CAD? AUD? -> unknown
