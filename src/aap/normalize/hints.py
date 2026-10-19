"""Supplier country hint from supplier-side fields only (never the bill-to address,
which is our own company). Used for date order and decimal separators."""

from __future__ import annotations

import re

from aap.common.taxid import classify_tax_id

US_STATES = ["AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"]
US_ADDR = re.compile(r"\b(" + "|".join(US_STATES) + r")\s+\d{5}(-\d{4})?\b")
IN_ADDR = re.compile(r"(?i)\b(india|maharashtra|karnataka|tamil nadu|telangana|gujarat|delhi|pune|mumbai|bengaluru|"
                     r"bangalore|chennai|hyderabad|kolkata|noida)\b")
DE_ADDR = re.compile(r"(?i)\b(germany|deutschland|stra(ss|ß)e|str\.)\b|\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß]+\b")
GB_ADDR = re.compile(r"(?i)\b(united kingdom|england|scotland|wales|uk)\b|\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b")
COUNTRY_WORDS = {"united states": "US", "usa": "US", "u.s.a": "US", "india": "IN", "germany": "DE",
                 "deutschland": "DE", "france": "FR", "netherlands": "NL", "ireland": "IE", "united kingdom": "GB"}


def country_hint(*, vendor_address: str | None = None, vendor_tax_id: str | None = None,
                 vendor_phone: str | None = None, currency_text: str | None = None,
                 remit_to: str | None = None) -> str | None:
    kind = classify_tax_id(vendor_tax_id)
    if kind == "EIN":
        return "US"
    if kind == "GSTIN":
        return "IN"
    if kind == "VAT" and vendor_tax_id:
        cc = vendor_tax_id.strip().upper()[:2]
        return "GR" if cc == "EL" else cc
    for text in (vendor_address, remit_to):
        if not text:
            continue
        low = text.lower()
        for word, cc in COUNTRY_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", low):
                return cc
        if US_ADDR.search(text):
            return "US"
        if IN_ADDR.search(text):
            return "IN"
        if DE_ADDR.search(text):
            return "DE"
        if GB_ADDR.search(text):
            return "GB"
    if vendor_phone:
        if vendor_phone.strip().startswith("+91"):
            return "IN"
        if vendor_phone.strip().startswith("+49"):
            return "DE"
        if vendor_phone.strip().startswith("+44"):
            return "GB"
    if currency_text:
        if "₹" in currency_text or currency_text.strip().upper() in ("INR", "RS", "RS."):
            return "IN"
        if "£" in currency_text:
            return "GB"
    return None
