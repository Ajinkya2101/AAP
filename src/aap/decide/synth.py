"""Synthetic training invoices for adapter B: copies of real train invoices (PLAN-MODEL-JSON §7).

Each synthetic invoice is a copy of one real train invoice of the same supplier, its *parent*:

- unchanged: the supplier, site, OU, currency, source, invoice type, terms, payment method and pay
  group; the lines' number, type, distribution set and account; the distributions' account and
  liability account. Every Oracle value of a synthetic invoice is therefore one AP really keyed.
- generated: a new INVOICE_ID (from SYNTHETIC_ID_BASE up, never a real one), an invoice number in the
  parent's pattern (each digit replaced, letters and separators kept), the invoice date (the parent's
  + 0-14 days, never earlier), the GL and terms dates (the parent's gaps kept), the amounts (one factor of 0.6-1.4
  per invoice; distributions add up to their line) and each line's description (a real train line
  description of the same supplier, line type and account or set, so the blank rate is the real one).
- empty: payment status (AMOUNT_PAID, DISCOUNT_AMOUNT_TAKEN) and the distributions' audit columns.

Every row carries SYNTHETIC = "Y", PARENT_INVOICE_ID and GEN_VERSION. Rules on their use: they are for
B's training only, never in a context pack's history (the history comes from the real delivery), never
in dev or test. A copy's context pack is its parent's: as of the parent's GL date, the parent left out.
So a copy has exactly the candidates its parent had when AP keyed it, and an account AP used for the
first time is not a candidate for the copy either (the target is null, as it is for the parent). A pack
as of the copy's own (later) date would only miss the invoices in between, never see later ones.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

GEN_VERSION = "synth/1"
SYNTHETIC_ID_BASE = 900_000_000
FACTOR = (0.6, 1.4)
DATE_JITTER_DAYS = 14
CENT = Decimal("0.01")

HEADER_GENERATED = ("INVOICE_ID", "INVOICE_NUM", "INVOICE_DATE", "GL_DATE", "TERMS_DATE", "INVOICE_AMOUNT",
                    "AMOUNT_APPLICABLE_TO_DISCOUNT", "APPROVED_AMOUNT", "BASE_AMOUNT")
HEADER_EMPTY = ("AMOUNT_PAID", "DISCOUNT_AMOUNT_TAKEN", "CANCELLED_DATE")
DIST_COLUMNS = ("INVOICE_DISTRIBUTION_ID", "INVOICE_ID", "INVOICE_LINE_NUMBER", "DISTRIBUTION_LINE_NUMBER",
                "LINE_TYPE_LOOKUP_CODE", "DIST_CODE_COMBINATION_ID", "ACCTS_PAY_CODE_COMBINATION_ID", "AMOUNT",
                "BASE_AMOUNT", "QUANTITY_INVOICED", "UNIT_PRICE", "DESCRIPTION", "ACCOUNTING_DATE", "PERIOD_NAME",
                "REVERSAL_FLAG", "ORG_ID", "SET_OF_BOOKS_ID")
MARKERS = ("SYNTHETIC", "PARENT_INVOICE_ID", "GEN_VERSION")

Row = dict[str, Any]


@dataclass
class Invoice:
    header: Row
    lines: list[Row]
    dists: list[Row]


def _num(v: Any) -> int | None:
    from aap.clientdata.readers import num

    return num(v)


def _dec(v: Any) -> Decimal | None:
    from aap.clientdata.readers import dec

    return dec(v)


def _day(v: Any) -> date | None:
    from aap.clientdata.readers import day

    return day(v)


def _live(dists: list[Row]) -> list[Row]:
    """Reversal pairs net to zero and are left out, as the target and the history leave them out."""
    return [x for x in dists if (x.get("REVERSAL_FLAG") or "N") != "Y"]


def line_key(vendor_id: Any, line: Row, dists: list[Row]) -> tuple:
    """What a line description belongs to: the supplier, the line type and what the line was coded to
    (its distribution set, else its largest account)."""
    sid = _num(line.get("DISTRIBUTION_SET_ID"))
    own = [x for x in _live(dists) if _num(x.get("INVOICE_LINE_NUMBER")) == _num(line.get("LINE_NUMBER"))]
    top = max(own, key=lambda x: abs(_dec(x.get("AMOUNT")) or 0), default=None)
    coded = ("set", sid) if sid is not None else ("ccid", _num(top.get("DIST_CODE_COMBINATION_ID")) if top else None)
    return (_num(vendor_id), str(line.get("LINE_TYPE_LOOKUP_CODE") or "ITEM").upper(), coded)


def description_pool(invoices: list[Invoice]) -> dict[tuple, list[str | None]]:
    """line_key -> the descriptions (None for blank) of those lines in the given (train) invoices."""
    pool: dict[tuple, list[str | None]] = defaultdict(list)
    for inv in invoices:
        for ln in inv.lines:
            pool[line_key(inv.header.get("VENDOR_ID"), ln, inv.dists)].append(ln.get("DESCRIPTION"))
    return pool


def new_number(parent: Any, rnd: random.Random, taken: set[str]) -> str:
    """The parent's number with every digit replaced (a leading non-zero digit stays non-zero);
    letters and separators kept. Never a number in `taken`."""
    text = str(parent or "")
    if not any(c.isdigit() for c in text):
        text = f"{text}-0000" if text else "0000000"
    for _ in range(1000):
        out, first = [], True
        for c in text:
            if c.isdigit():
                lo = 1 if first and c != "0" else 0
                out.append(str(rnd.randint(lo, 9)))
                first = False
            else:
                out.append(c)
        candidate = "".join(out)
        if candidate != str(parent) and candidate not in taken:
            taken.add(candidate)
            return candidate
    raise RuntimeError(f"no free invoice number in the pattern of {parent!r}")


def _scale(v: Any, factor: Decimal) -> Decimal | None:
    d = _dec(v)
    return None if d is None else (d * factor).quantize(CENT, rounding=ROUND_HALF_UP)


def _period(d: date) -> str:
    return d.strftime("%b-%y").upper()


def clone(parent: Invoice, *, invoice_id: int, first_dist_id: int, rnd: random.Random,
          window: tuple[date, date], descriptions: dict[tuple, list[str | None]], taken_numbers: set[str]
          ) -> Invoice:
    """One synthetic invoice from `parent` (see the module docstring)."""
    h = parent.header
    factor = Decimal(str(round(rnd.uniform(*FACTOR), 4)))
    p_inv, p_gl, p_terms = _day(h.get("INVOICE_DATE")), _day(h.get("GL_DATE")), _day(h.get("TERMS_DATE"))
    shift = timedelta(days=rnd.randint(0, DATE_JITTER_DAYS))
    if p_gl and p_gl + shift > window[1]:
        shift = max(window[1] - p_gl, timedelta(0))      # the GL date stays in the extract's window
    inv_date = (p_inv or window[0]) + shift
    gl_date = (p_gl + shift) if p_gl else inv_date
    terms_date = inv_date + ((p_terms - p_inv) if p_terms and p_inv else timedelta(0))
    markers = {"SYNTHETIC": "Y", "PARENT_INVOICE_ID": _num(h.get("INVOICE_ID")), "GEN_VERSION": GEN_VERSION}

    lines, dists, next_dist = [], [], first_dist_id
    live = _live(parent.dists)
    for ln in sorted(parent.lines, key=lambda x: _num(x.get("LINE_NUMBER")) or 0):
        n = _num(ln.get("LINE_NUMBER"))
        amount = _scale(ln.get("AMOUNT"), factor) or Decimal(0)
        pool = descriptions.get(line_key(h.get("VENDOR_ID"), ln, parent.dists))
        desc = rnd.choice(pool) if pool else ln.get("DESCRIPTION")
        lines.append({**ln, "INVOICE_ID": invoice_id, "AMOUNT": amount,
                      "BASE_AMOUNT": amount if ln.get("BASE_AMOUNT") is not None else None,
                      "DESCRIPTION": desc, "ACCOUNTING_DATE": gl_date, "PERIOD_NAME": _period(gl_date), **markers})
        own = [x for x in live if _num(x.get("INVOICE_LINE_NUMBER")) == n]
        scaled = [_scale(x.get("AMOUNT"), factor) or Decimal(0) for x in own]
        if scaled:
            scaled[-1] += amount - sum(scaled, Decimal(0))          # the distributions add up to the line
        for x, a in zip(own, scaled, strict=True):
            row = {c: x.get(c) for c in DIST_COLUMNS}
            row.update({"INVOICE_DISTRIBUTION_ID": next_dist, "INVOICE_ID": invoice_id, "AMOUNT": a,
                        "BASE_AMOUNT": a if x.get("BASE_AMOUNT") is not None else None,
                        "UNIT_PRICE": _scale(x.get("UNIT_PRICE"), factor), "DESCRIPTION": desc,
                        "ACCOUNTING_DATE": gl_date, "PERIOD_NAME": _period(gl_date), **markers})
            dists.append(row)
            next_dist += 1
    total = sum((ln["AMOUNT"] for ln in lines), Decimal(0))
    header = {**h, "INVOICE_ID": invoice_id,
              "INVOICE_NUM": new_number(h.get("INVOICE_NUM"), rnd, taken_numbers),
              "INVOICE_DATE": inv_date, "GL_DATE": gl_date, "TERMS_DATE": terms_date if p_terms else None,
              "INVOICE_AMOUNT": total,
              "AMOUNT_APPLICABLE_TO_DISCOUNT": _scale(h.get("AMOUNT_APPLICABLE_TO_DISCOUNT"), factor),
              "APPROVED_AMOUNT": _scale(h.get("APPROVED_AMOUNT"), factor),
              "BASE_AMOUNT": total if h.get("BASE_AMOUNT") is not None else None,
              **dict.fromkeys(HEADER_EMPTY), **markers}
    return Invoice(header, lines, dists)


def parents_for(real_train: list[Any], count: int, rnd: random.Random) -> list[Any]:
    """`count` parents taken in turn from a seeded shuffle of the real train invoices, so each is used
    about equally often (accounts appear as often as AP used them)."""
    if not real_train or count <= 0:
        return []
    order = list(real_train)
    rnd.shuffle(order)
    return [order[i % len(order)] for i in range(count)]
