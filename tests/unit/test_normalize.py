from datetime import date
from decimal import Decimal

import pytest

from aap.normalize.amounts import parse_amount, parse_percent, parse_quantity
from aap.normalize.currency import resolve_currency
from aap.normalize.dates import parse_date
from aap.normalize.hints import country_hint


@pytest.mark.parametrize("text,sep,expected", [
    ("$14,904.00", None, "14904.00"),
    ("$1,150.00", None, "1150.00"),
    ("3.400,00 €", ",", "3400.00"),
    ("85,00", None, "85.00"),
    ("1,280", None, "1280"),
    ("1.234", ",", "1234"),
    ("0.42", None, "0.42"),
    ("(1,234.50)", None, "-1234.50"),
    ("₹1,18,000.00", None, "118000.00"),
    ("Rs. 2,500", None, "2500"),
    ("USD 2,752.25", None, "2752.25"),
    ("1 234,56", ",", "1234.56"),
    ("", None, None),
    ("n/a", None, None),
])
def test_parse_amount(text, sep, expected):
    value = parse_amount(text, sep)
    assert (str(value) if value is not None else None) == expected


def test_percent_and_quantity():
    assert parse_percent("Sales tax 8%") == Decimal("8")
    assert parse_percent("8.875 %") == Decimal("8.875")
    assert parse_quantity("10 pcs") == Decimal("10")
    assert parse_quantity(None) is None


@pytest.mark.parametrize("text,country,expected,ambiguous", [
    ("12-Sep-2026", None, date(2026, 9, 12), False),
    ("September 14, 2026", None, date(2026, 9, 14), False),
    ("Sept 12, 2026", None, date(2026, 9, 12), False),
    ("01 Sep 2026", None, date(2026, 9, 1), False),
    ("2026-09-11", None, date(2026, 9, 11), False),
    ("17.09.2026", "DE", date(2026, 9, 17), False),
    ("08/06/2026", "US", date(2026, 8, 6), False),
    ("08/06/2026", "IN", date(2026, 6, 8), False),
    ("03/04/2026", None, date(2026, 3, 4), True),        # S9
    ("09/15/2026", None, date(2026, 9, 15), False),      # only one valid reading
    ("15/09/2026", None, date(2026, 9, 15), False),
])
def test_parse_date(text, country, expected, ambiguous):
    assert parse_date(text, country) == (expected, ambiguous)


def test_currency():
    assert resolve_currency("$") == ("USD", ["USD", "CAD", "AUD", "SGD", "NZD", "HKD", "MXN"])
    assert resolve_currency("$", vendor_default="CAD")[0] == "CAD"
    assert resolve_currency("EUR") == ("EUR", [])
    assert resolve_currency("€") == ("EUR", [])
    assert resolve_currency("₹") == ("INR", [])
    assert resolve_currency(None) == (None, [])


def test_country_hint_uses_supplier_fields_only():
    assert country_hint(vendor_tax_id="84-1234567") == "US"
    assert country_hint(vendor_tax_id="DE287445193") == "DE"
    assert country_hint(vendor_address="233 S Wacker Dr, Chicago, IL 60606") == "US"
    assert country_hint(vendor_address="Survey No 12, Pune - 411057, Maharashtra, India") == "IN"
    assert country_hint(vendor_address=None, vendor_tax_id=None) is None
