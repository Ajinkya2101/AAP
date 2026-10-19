"""Dates: dateparser with the supplier country as a hint; flag two_date_formats."""

from __future__ import annotations

import contextlib
import re
from datetime import date, datetime

import dateparser

NUMERIC = re.compile(r"^\s*(\d{1,2})[./\-](\d{1,2})[./\-](\d{2}|\d{4})\s*$")
ISO = re.compile(r"^\s*(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})\s*$")

MDY_COUNTRIES = {"US", "PH", "FM", "MH", "PW"}
YMD_COUNTRIES = {"CN", "JP", "KR", "TW", "HU", "LT", "SE"}


def date_order_for(country: str | None) -> str | None:
    if not country:
        return None
    c = country.upper()
    if c in MDY_COUNTRIES:
        return "MDY"
    if c in YMD_COUNTRIES:
        return "YMD"
    return "DMY"


def _year(y: str) -> int:
    n = int(y)
    return n + 2000 if n < 100 else n


def parse_date(text: str | None, country: str | None = None) -> tuple[date | None, bool]:
    """Returns (date, ambiguous). ambiguous=True when a numeric date has two valid
    readings and there is no country hint (the value then uses month-first)."""
    if not text:
        return None, False
    s = text.strip()
    m = ISO.match(s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))), False
        except ValueError:
            return None, False
    m = NUMERIC.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        order = date_order_for(country)
        readings = []
        for month, day, label in ((a, b, "MDY"), (b, a, "DMY")):
            with contextlib.suppress(ValueError):
                readings.append((label, date(y, month, day)))
        if not readings:
            return None, False
        distinct = {d for _, d in readings}
        if len(distinct) == 1:
            return readings[0][1], False
        if order in ("MDY", "DMY"):
            return next(d for label, d in readings if label == order), False
        return next(d for label, d in readings if label == "MDY"), True
    order = date_order_for(country)
    settings = {"PREFER_DAY_OF_MONTH": "first", "RETURN_AS_TIMEZONE_AWARE": False, "STRICT_PARSING": False,
                "REQUIRE_PARTS": ["day", "month", "year"]}
    if order:
        settings["DATE_ORDER"] = order
    parsed = dateparser.parse(s.replace("Sept ", "Sep ").replace("Sept.", "Sep."), settings=settings)
    if parsed is None:
        return None, False
    return (parsed.date() if isinstance(parsed, datetime) else parsed), False
