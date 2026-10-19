"""Amounts: strip symbols and separators, return Decimal (never float)."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_CURRENCY = re.compile(r"(?i)(us\$|a\$|c\$|s\$|rs\.?|inr|usd|eur|gbp|jpy|cad|aud|sgd|chf|aed|[$€£₹¥])")
_KEEP = re.compile(r"[^0-9.,'\s\-()]")

# Countries that write 1.234,56
DECIMAL_COMMA = {"DE", "FR", "NL", "ES", "IT", "AT", "BE", "PT", "DK", "SE", "FI", "NO", "PL", "CZ", "BR", "TR",
                 "ID", "RU"}


def decimal_sep_for(country: str | None) -> str | None:
    if not country:
        return None
    return "," if country.upper() in DECIMAL_COMMA else "."


def parse_amount(text: str | None, decimal_sep: str | None = None) -> Decimal | None:
    if text is None:
        return None
    s = _CURRENCY.sub("", str(text)).strip()
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.endswith("-"):
        negative, s = True, s[:-1]
    if s.startswith("-"):
        negative, s = True, s[1:]
    s = _KEEP.sub("", s).replace("'", "").replace(" ", "").replace(" ", "").strip("()")
    if not s or not re.search(r"\d", s):
        return None

    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = s.split(sep)
        if len(parts) > 2:
            dec = None                         # 1,234,567 -> thousands only
        elif decimal_sep is not None:
            # A hint decides, except when the digit count makes it impossible (1,234 with ',' decimal is fine).
            dec = sep if decimal_sep == sep or len(parts[1]) != 3 else None
        else:
            dec = sep if len(parts[1]) != 3 else None
    else:
        dec = None

    if dec is None:
        digits = s.replace(",", "").replace(".", "")
    else:
        thousands = "," if dec == "." else "."
        digits = s.replace(thousands, "").replace(dec, ".")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None
    return -value if negative else value


def parse_percent(text: str | None) -> Decimal | None:
    if not text:
        return None
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", text)
    if not m:
        m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*", text)
    if not m:
        return None
    return Decimal(m.group(1).replace(",", "."))


def parse_quantity(text: str | None) -> Decimal | None:
    if text is None:
        return None
    m = re.search(r"-?\d[\d,.']*", str(text))
    if not m:
        return None
    return parse_amount(m.group(0))
