"""Payment term lines read from a term's name and description, for terms delivered without
AP_TERMS_LINES (pay_terms.xlsx has the headers only; the lines are asked for, B-5).

    "30 NET"           NET DUE IN 30 DAYS                      -> due in 30 days
    "30 NET 2%10"      NET DUE IN 30 DAYS 2% DISCOUNT IN 10    -> 30 days, 2 % within 10 days
    "15 EOM"           NET DUE ON THE 15TH AFTER MONTH END     -> 15th of the next month
    "6 Months"         6 Months 25th of Month                  -> 25th, 6 months on
    "30 DIAS", "CONTADO", "Immediate"                          -> 30 / 0 / 0 days

A derived line is a guess until the real AP_TERMS_LINES arrive and replace it; `derive_term_line`
returns None when neither text says enough, and the term then has no days (it is still matched by
name, never by a due date).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .readers import Delivery, day, dec, num


@dataclass(frozen=True)
class TermLine:
    due_percent: Decimal = Decimal(100)
    due_days: int | None = None
    due_day_of_month: int | None = None
    due_months_forward: int | None = None
    discount_percent: Decimal | None = None
    discount_days: int | None = None


_DAYS = re.compile(r"(?i)\b(?:net\s+due\s+in|due\s+in|net)\s+(\d{1,3})\s*(?:days?|dyas)?\b|\b(\d{1,3})\s*(?:net|dias|days)\b")
_DISCOUNT = re.compile(r"(?i)(\d+(?:\.\d+)?|\.\d+)\s*%\s*(?:discount\s*)?(?:in\s*|within\s*)?(\d{1,3})?")
_EOM = re.compile(r"(?i)(?:on\s+the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:after\s+(?:\w+\s+){0,2}month\s+end|eom\b)"
                  r"|\b(\d{1,2})\s+eom\b")
_MONTHS = re.compile(r"(?i)\b(\d{1,2})\s+months?\b.*?\b(\d{1,2})(?:st|nd|rd|th)\b")
_IMMEDIATE = re.compile(r"(?i)\b(immediate|contado|cod|cash on delivery|due on receipt|upon receipt)\b")


def derive_term_line(name: str, description: str | None = None) -> TermLine | None:
    texts = [t for t in (description, name) if t]
    for text in texts:
        if _IMMEDIATE.search(text):
            return TermLine(due_days=0)
    for text in texts:
        m = _MONTHS.search(text)
        if m:
            return TermLine(due_months_forward=int(m.group(1)), due_day_of_month=int(m.group(2)))
        m = _EOM.search(text)
        if m:
            dom = int(m.group(1) or m.group(2))
            # "90 EOM": no 90th of a month; days after month end cannot be told from the text alone
            return TermLine(due_day_of_month=dom, due_months_forward=1) if dom <= 31 else None
    for text in texts:
        m = _DAYS.search(text)
        if not m:
            continue
        days = int(m.group(1) or m.group(2))
        rest = text[m.end():]
        d = _DISCOUNT.search(rest) or _DISCOUNT.search(text[:m.start()])
        if d:
            pct = Decimal(d.group(1))
            disc_days = int(d.group(2)) if d.group(2) else None
            if disc_days is None:            # "30 NET 2% 14": the days after the percent in the name
                n = re.search(r"%\s*(\d{1,3})", name)
                disc_days = int(n.group(1)) if n else None
            return TermLine(due_days=days, discount_percent=pct, discount_days=disc_days)
        return TermLine(due_days=days)
    return None


# ---------------------------------------------------------- mock EBS rows
def term_rows(delivery: Delivery) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """AP_TERMS (+ AP_TERMS_LINES, else lines derived from the names) of a delivery as rows of the
    mock's ap.ap_terms_tl / ap.ap_terms_lines. Returns the rows and the names of the terms whose
    lines were derived (to be replaced when the real lines arrive) or are unknown."""
    real: dict[int, list[dict[str, Any]]] = {}
    for r in delivery.rows("AP_TERMS_LINES"):
        real.setdefault(num(r["TERM_ID"]), []).append(r)  # type: ignore[arg-type]
    headers, lines, notes = [], [], []
    for rank, r in enumerate(sorted(delivery.rows("AP_TERMS"), key=lambda x: num(x["TERM_ID"]) or 0), start=1):
        term_id = num(r["TERM_ID"])
        end = day(r.get("END_DATE_ACTIVE"))
        headers.append(dict(term_id=term_id, language="US", source_lang="US", name=r["NAME"],
                            description=r.get("DESCRIPTION"), enabled_flag=r.get("ENABLED_FLAG") or "Y",
                            type=r.get("TYPE") or "STANDARD", rank=num(r.get("RANK")) or rank,
                            due_cutoff_day=num(r.get("DUE_CUTOFF_DAY")),
                            start_date_active=day(r.get("START_DATE_ACTIVE")), end_date_active=end))
        if term_id in real:
            for x in sorted(real[term_id], key=lambda x: num(x["SEQUENCE_NUM"]) or 0):
                lines.append(dict(term_id=term_id, sequence_num=num(x["SEQUENCE_NUM"]),
                                  due_percent=dec(x.get("DUE_PERCENT")), due_amount=dec(x.get("DUE_AMOUNT")),
                                  due_days=num(x.get("DUE_DAYS")), due_day_of_month=num(x.get("DUE_DAY_OF_MONTH")),
                                  due_months_forward=num(x.get("DUE_MONTHS_FORWARD")),
                                  discount_percent=dec(x.get("DISCOUNT_PERCENT")),
                                  discount_days=num(x.get("DISCOUNT_DAYS"))))
            continue
        t = derive_term_line(r["NAME"], r.get("DESCRIPTION"))
        notes.append(f"{r['NAME']}: {'derived from the name' if t else 'unknown (no lines, not parseable)'}")
        if t:
            lines.append(dict(term_id=term_id, sequence_num=1, due_percent=t.due_percent, due_amount=None,
                              due_days=t.due_days, due_day_of_month=t.due_day_of_month,
                              due_months_forward=t.due_months_forward, discount_percent=t.discount_percent,
                              discount_days=t.discount_days))
    return {"ap.ap_terms_tl": headers, "ap.ap_terms_lines": lines}, notes


def payment_method_rows(delivery: Delivery) -> dict[str, list[dict[str, Any]]]:
    """IBY_PAYMENT_METHODS (_B or _TL extract) as rows of the mock's iby.iby_payment_methods_b/_tl."""
    base, tl = [], []
    for r in delivery.rows("IBY_PAYMENT_METHODS"):
        code = r["PAYMENT_METHOD_CODE"]
        base.append(dict(payment_method_code=code, inactive_date=day(r.get("INACTIVE_DATE")),
                         support_bills_payable_flag=r.get("SUPPORT_BILLS_PAYABLE_FLAG") or "N",
                         seeded_flag=r.get("SEEDED_FLAG") or "N"))
        tl.append(dict(payment_method_code=code, language="US", source_lang="US",
                       payment_method_name=r.get("PAYMENT_METHOD_NAME") or code, description=r.get("DESCRIPTION")))
    return {"iby.iby_payment_methods_b": base, "iby.iby_payment_methods_tl": tl}
