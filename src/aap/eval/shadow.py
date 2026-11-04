"""Shadow comparison (IMPLEMENTATION-PLAN-CLIENT-DATA P9.3 / PLAN-MID-DECEMBER D4): the Oracle-ready
JSON next to the record AP keyed for the same invoice, field by field. This is the main evidence
for Siva: is the JSON what AP would have keyed? Pure functions; the harness passes both in.

Header: compared by the IDs of the instance the reference data came from (`source_ids`), since the
keyed record and the replica share them, plus the values themselves (number, dates, amount,
currency, type, payment method). A keyed value that is empty is not known, so it is not scored.
Lines: the multiset of line types and the amount per line type. Accounts: the GL result (eval/gl.py).

An invoice is "equal" when every core field, the line types, the amounts per type and (for non-PO
invoices) every keyed amount's account match. The GL date is compared but not part of "equal": it
follows a policy (B-9) rather than the page.

The state matters as much as equality: an invoice marked ready but not equal would have been
created wrong without anyone looking, so those are listed by name.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from aap.common.coa import CoaLayout

from .gl import GlResult, gl_result

ZERO = Decimal(0)
READY = {"ready", "approved"}


def _dec(v: Any) -> Decimal | None:
    try:
        return None if v in (None, "") else Decimal(str(v))
    except ArithmeticError:
        return None


def _norm(kind: str, v: Any) -> Any:
    if v in (None, ""):
        return None
    if kind == "amount":
        return _dec(v)
    if kind == "alnum":
        return re.sub(r"[^0-9A-Za-z]", "", str(v)).upper() or None
    if kind == "int":
        try:
            return int(v)
        except (TypeError, ValueError):
            return str(v)
    return str(v).strip().upper()


# (name, key in the truth's `expected`, (section, key) in the JSON, comparison, part of "equal")
HEADER_CHECKS: list[tuple[str, str, tuple[str, str], str, bool]] = [
    ("supplier", "vendor_id", ("source_ids", "vendor_id"), "int", True),
    ("site", "vendor_site_id", ("source_ids", "vendor_site_id"), "int", True),
    ("operating_unit", "org_id", ("source_ids", "org_id"), "int", True),
    ("invoice_num", "invoice_num", ("header", "invoice_num"), "alnum", True),
    ("invoice_date", "invoice_date", ("header", "invoice_date"), "text", True),
    ("invoice_amount", "invoice_amount", ("header", "invoice_amount"), "amount", True),
    ("currency", "currency_code", ("header", "invoice_currency_code"), "text", True),
    ("invoice_type", "invoice_type", ("header", "invoice_type_lookup_code"), "text", True),
    ("terms", "terms_id", ("source_ids", "terms_id"), "int", True),
    ("payment_method", "payment_method", ("header", "payment_method_code"), "text", True),
    ("gl_date", "gl_date", ("header", "gl_date"), "text", False),
]


@dataclass
class ShadowField:
    name: str
    expected: str | None
    actual: str | None
    correct: bool
    core: bool = True


@dataclass
class ShadowResult:
    state: str
    fields: list[ShadowField] = field(default_factory=list)
    gl: GlResult | None = None

    @property
    def equal(self) -> bool:
        return all(f.correct for f in self.fields if f.core) and (self.gl is None or self.gl.full_equal)

    @property
    def ready(self) -> bool:
        return self.state in READY


def _types(lines: list[dict[str, Any]], key: str) -> Counter:
    return Counter(str(ln.get(key) or "ITEM") for ln in lines)


def _amount_by_type(lines: list[dict[str, Any]], type_key: str) -> dict[str, Decimal]:
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for ln in lines:
        out[str(ln.get(type_key) or "ITEM")] += _dec(ln.get("amount")) or ZERO
    return {k: v for k, v in out.items() if v != 0}


def _fmt_types(c: Counter) -> str | None:
    return ",".join(f"{k}x{n}" for k, n in sorted(c.items())) or None


def _fmt_amounts(d: dict[str, Decimal]) -> str | None:
    return ",".join(f"{k}={v:.2f}" for k, v in sorted(d.items())) or None


def shadow_compare(truth: dict[str, Any], oracle_json: dict[str, Any], coa: CoaLayout) -> ShadowResult:
    exp = truth.get("expected") or {}
    r = ShadowResult(state=(oracle_json.get("status") or {}).get("state") or "not_ready")
    for name, key, (section, jkey), kind, core in HEADER_CHECKS:
        e = exp.get(key)
        if e in (None, ""):
            continue                                   # not known from the keyed record: not scored
        a = (oracle_json.get(section) or {}).get(jkey)
        r.fields.append(ShadowField(name, str(e), None if a in (None, "") else str(a),
                                    _norm(kind, e) == _norm(kind, a), core))
    keyed_lines = exp.get("lines")
    if keyed_lines:
        json_lines = oracle_json.get("lines") or []
        et, at = _types(keyed_lines, "line_type"), _types(json_lines, "line_type_lookup_code")
        r.fields.append(ShadowField("line_types", _fmt_types(et), _fmt_types(at), et == at))
        ea, aa = _amount_by_type(keyed_lines, "line_type"), _amount_by_type(json_lines, "line_type_lookup_code")
        r.fields.append(ShadowField("amount_by_line_type", _fmt_amounts(ea), _fmt_amounts(aa), ea == aa))
    r.gl = gl_result(exp, oracle_json, coa)
    return r


def summarize_shadow(results: list[tuple[str, ShadowResult]]) -> dict[str, Any] | None:
    """The shadow block of the eval metrics: (document name, result) pairs; None when there are none."""
    if not results:
        return None

    def ratio(a: int, b: int) -> float | None:
        return round(a / b, 4) if b else None

    per: dict[str, list[bool]] = defaultdict(list)
    for _, r in results:
        for f in r.fields:
            per[f.name].append(f.correct)
    ready = [(n, r) for n, r in results if r.ready]
    return {
        "invoices": len(results),
        "equal": ratio(sum(r.equal for _, r in results), len(results)),
        "ready": ratio(len(ready), len(results)),
        "ready_and_equal": ratio(sum(r.equal for _, r in ready), len(ready)),
        "ready_but_different": [n for n, r in ready if not r.equal],
        "states": dict(Counter(r.state for _, r in results)),
        "fields": {k: ratio(sum(v), len(v)) for k, v in sorted(per.items())},
    }
