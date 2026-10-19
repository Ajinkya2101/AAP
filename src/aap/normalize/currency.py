"""Currency: symbol or code -> ISO 4217. An ambiguous symbol uses the vendor
default currency (decided at stage 7 when the vendor is known)."""

from __future__ import annotations

import re

from babel.numbers import get_currency_name

SYMBOLS: dict[str, list[str]] = {
    "$": ["USD", "CAD", "AUD", "SGD", "NZD", "HKD", "MXN"],
    "US$": ["USD"], "USD$": ["USD"], "C$": ["CAD"], "CA$": ["CAD"], "A$": ["AUD"], "AU$": ["AUD"], "S$": ["SGD"],
    "€": ["EUR"], "£": ["GBP"], "₹": ["INR"], "RS": ["INR"], "RS.": ["INR"], "¥": ["JPY", "CNY"],
    "FR.": ["CHF"], "AED": ["AED"], "DHS": ["AED"],
}
COUNTRY_CURRENCY = {"US": "USD", "IN": "INR", "DE": "EUR", "FR": "EUR", "NL": "EUR", "IE": "EUR", "GB": "GBP",
                    "CA": "CAD", "AU": "AUD", "SG": "SGD", "JP": "JPY", "CH": "CHF", "AE": "AED"}


def is_iso_code(code: str) -> bool:
    code = code.upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        return False
    try:
        return get_currency_name(code, locale="en") != code
    except Exception:  # noqa: BLE001
        return False


def resolve_currency(text: str | None, *, vendor_default: str | None = None,
                     country: str | None = None) -> tuple[str | None, list[str]]:
    """Returns (ISO code or None, candidates when ambiguous)."""
    if not text:
        return None, []
    raw = text.strip()
    m = re.search(r"\b([A-Za-z]{3})\b", raw)
    if m and is_iso_code(m.group(1)):
        return m.group(1).upper(), []
    key = raw.upper().replace(" ", "")
    candidates = SYMBOLS.get(key)
    if candidates is None:
        for sym, cands in SYMBOLS.items():
            if sym in key:
                candidates = cands
                break
    if not candidates:
        return None, []
    if len(candidates) == 1:
        return candidates[0], []
    if vendor_default and vendor_default in candidates:
        return vendor_default, candidates
    hinted = COUNTRY_CURRENCY.get((country or "").upper())
    if hinted in candidates:
        return hinted, candidates
    return candidates[0], candidates
