"""Regex fallback extractor used only by the stub LLM when no ground truth exists.

Header fields only; good enough to push unlabelled documents through the
pipeline without a GPU. Never used with LLM_BACKEND=vllm.
"""

from __future__ import annotations

import re
from typing import Any

from aap.schemas.source import HEADER_FIELDS

# Money always has two decimals here, so IDs and years are not read as amounts.
AMOUNT = r"([$€£₹]?\s?-?\d{1,3}(?:[,.\s]\d{3})*[.,]\d{2})\b"
PATTERNS = {
    "invoice_number": r"(?i)invoice\s*(?:no\.?|number|num|#)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-/]{2,})",
    "invoice_date": r"(?i)(?:invoice\s+)?date\s*[:]?\s*([0-9]{1,4}[./\-][0-9A-Za-z]{1,9}[./\-][0-9]{2,4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4})",
    "po_number": r"(?i)(?:p\.?\s?o\.?|purchase\s+order|order)\s*(?:no\.?|number|#)?\s*[:#]?\s*(\d{5,10})",
    "subtotal": rf"(?i)sub\s*-?\s*total\s*[:]?\s*{AMOUNT}",
    "tax_amount": rf"(?i)(?:sales\s+tax|vat|gst|igst|\btax)(?!\s*(?:id|no|number|\())[^\n\d$€£₹]{{0,20}}(?:\d+(?:\.\d+)?\s*%)?\s*[:]?\s*{AMOUNT}",
    "payment_terms": r"(?i)terms\s*[:]?\s*([^\n]{3,60})",
    "vendor_tax_id": r"\b(\d{2}-\d{7}|\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]|(?:DE|FR|NL|GB|IE)\d{8,12}[A-Z0-9]*)\b",
    "vendor_email": r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})",
}


TOTAL = rf"(?i)(?<![a-z-])(?:total\s+due|amount\s+due|balance\s+due|grand\s+total|invoice\s+total|total)\s*[:]?\s*(?:[A-Z]{{3}}\s*)?{AMOUNT}"


def heuristic_extract(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {name: None for name in HEADER_FIELDS}
    for name, pattern in PATTERNS.items():
        m = re.search(pattern, text)
        if m:
            out[name] = m.group(1).strip()
    totals = re.findall(TOTAL, text)          # the grand total is the last total on the page
    out["total"] = totals[-1].strip() if totals else None
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), None)
    out["vendor_name"] = first[:120] if first else None
    if out["total"]:
        m = re.search(r"[$€£₹]", out["total"])
        out["currency"] = m.group(0) if m else None
    if not out["currency"]:
        m = re.search(r"\b(USD|EUR|GBP|INR|JPY|CAD|AUD|SGD|CHF|AED)\b", text)
        out["currency"] = m.group(1) if m else None
    out["lines"] = []
    return out
