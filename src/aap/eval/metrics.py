"""Eval metrics (PLAN 4 decision gates). Pure functions: truth sidecar vs pipeline output.

Field results are (field_path, expected, actual, correct). A field whose truth is empty
counts as correct only if the pipeline also left it empty (catches invented values,
e.g. a bank account on an invoice that has none).
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from rapidfuzz import fuzz

from aap.common.text import name_key
from aap.normalize.amounts import parse_amount, parse_quantity

# Gates (PLAN 4). Placeholders until finance agrees real ones.
GATES = {"header_accuracy": 0.97, "line_f1": 0.95}
# PLAN 4 "header fields": vendor, invoice #, date, total, tax.
GATE_HEADER_FIELDS = ["header.vendor_id", "header.invoice_num", "header.invoice_date", "header.invoice_amount",
                      "header.tax_amount"]


# Stage 8 done. With `--agents` the invoice has moved on to the agents' outcome by the time a run is rescored.
VALIDATED = {"VALIDATED", "AGENTS_RUNNING", "IN_REVIEW", "ON_HOLD"}


@dataclass
class FieldResult:
    path: str
    expected: str | None
    actual: str | None
    correct: bool


@dataclass
class DocResult:
    name: str
    tier: str                        # digital | scanned
    status: str
    fields: list[FieldResult] = field(default_factory=list)
    line_tp: int = 0
    line_pred: int = 0
    line_truth: int = 0
    line_invented: int = 0
    duration_ms: int | None = None
    error: str | None = None


def _alnum(v: Any) -> str | None:
    s = re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()
    return s or None


def _amount(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    return v if isinstance(v, Decimal) else parse_amount(str(v))


def _same(kind: str, expected: Any, actual: Any) -> bool:
    if kind == "amount":
        e, a = _amount(expected), _amount(actual)
    elif kind == "name":
        e, a = name_key(expected) or None, name_key(actual) or None
    elif kind == "lower":
        e = str(expected).strip().lower() if expected else None
        a = str(actual).strip().lower() if actual else None
    elif kind == "alnum":
        e, a = _alnum(expected), _alnum(actual)
    else:
        e = None if expected in (None, "") else str(expected)
        a = None if actual in (None, "") else str(actual)
    return e == a


# Extraction (stage 5) output vs the truth strings; normalized so formatting does not count.
EXTRACT_FIELDS = {
    "vendor_name": "name", "vendor_tax_id": "alnum", "vendor_email": "lower", "invoice_number": "alnum",
    "po_number": "alnum", "subtotal": "amount", "tax_amount": "amount", "total": "amount",
    "bank_account": "alnum", "iban": "alnum",
}
# Canonical invoice after normalize + map (stages 6-7) vs the expected EBS values.
HEADER_FIELDS = {
    "vendor_id": "exact", "vendor_site_id": "exact", "invoice_date": "exact", "invoice_amount": "amount",
    "subtotal": "amount", "tax_amount": "amount", "currency_code": "exact", "po_number": "alnum",
}


def field_results(truth: dict[str, Any], source: dict[str, Any] | None,
                  canonical: dict[str, Any] | None) -> list[FieldResult]:
    out: list[FieldResult] = []
    src_truth, expected = truth.get("source") or {}, truth.get("expected") or {}
    source = source or {}
    for name, kind in EXTRACT_FIELDS.items():
        e, a = src_truth.get(name), source.get(name)
        out.append(FieldResult(f"extract.{name}", _s(e), _s(a), _same(kind, e, a)))
    canon = canonical or {}

    def cval(name: str) -> Any:
        f = canon.get(name)
        return f.get("value") if isinstance(f, dict) else None

    for name, kind in HEADER_FIELDS.items():
        e, a = expected.get(name), cval(name)
        if name == "tax_amount" and e in (None, "") and _amount(a) == 0:
            a = None                                  # "no tax" and "tax 0.00" mean the same
        out.append(FieldResult(f"header.{name}", _s(e), _s(a), _same(kind, e, a)))
    e, a = src_truth.get("invoice_number"), cval("invoice_num")
    out.append(FieldResult("header.invoice_num", _s(e), _s(a), _same("alnum", e, a)))
    return out


def flatten_source(stored: dict[str, Any] | None) -> dict[str, Any]:
    """Stored SourceInvoice ({"field": {"value": ...}}) -> plain strings, lines included."""
    def val(v: Any) -> Any:
        return v.get("value") if isinstance(v, dict) else v

    if not stored:
        return {}
    out = {k: val(v) for k, v in stored.items() if k != "lines"}
    out["lines"] = [{k: val(v) for k, v in ln.items()} for ln in stored.get("lines") or []]
    return out


def _s(v: Any) -> str | None:
    return None if v is None or v == "" else str(v)


def _raw_key(ln: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    q = ln.get("quantity")
    return (parse_quantity(str(q)) if q not in (None, "") else None, _amount(ln.get("unit_price")),
            _amount(ln.get("amount")))


def _line_key(ln: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    """A line with only an amount means quantity 1 at that price: "amount only" and
    "qty 1 x price = amount" are the same line for the F1 (invented values are counted
    separately by invented_line_values)."""
    q, p, a = _raw_key(ln)
    if a is not None and ((q is None and p in (None, a)) or (q == 1 and p is None)):
        return (Decimal(1), a, a)
    return (q, p, a)


def invented_line_values(truth_lines: list[dict[str, Any]], pred_lines: list[dict[str, Any]]) -> int:
    """Quantity / unit price values the model returned for lines that show none (verbatim contract)."""
    n = 0
    for t, p in zip(truth_lines, pred_lines, strict=False):
        tq, tp, _ = _raw_key(t)
        pq, pp, _ = _raw_key(p)
        n += (tq is None and pq is not None) + (tp is None and pp is not None)
    return n


def line_match(truth_lines: list[dict[str, Any]], pred_lines: list[dict[str, Any]],
               min_desc: float = 85.0) -> int:
    """True positives: a predicted line with the same quantity, unit price and amount as a
    truth line and a similar description (PLAN 4: description + qty + unit price + amount)."""
    used: set[int] = set()
    tp = 0
    for t in truth_lines:
        tk = _line_key(t)
        best, best_score = None, 0.0
        for i, p in enumerate(pred_lines):
            if i in used or _line_key(p) != tk:
                continue
            score = fuzz.token_set_ratio((t.get("description") or "").lower(), (p.get("description") or "").lower())
            if score >= min_desc and score > best_score:
                best, best_score = i, score
        if best is not None:
            used.add(best)
            tp += 1
    return tp


def validation_results(truth: dict[str, Any], results: list[dict[str, Any]]) -> list[FieldResult]:
    exp = truth.get("expected") or {}
    fails = sorted({r["rule_id"] for r in results if r["result"] == "fail"})
    warns = {r["rule_id"] for r in results if r["result"] == "warning"}
    want_fail = sorted(exp.get("expect_fail") or [])
    want_warn = set(exp.get("expect_warn") or [])
    return [FieldResult("validation.fails", ",".join(want_fail) or None, ",".join(fails) or None, fails == want_fail),
            FieldResult("validation.warnings", ",".join(sorted(want_warn)) or None, ",".join(sorted(warns)) or None,
                        want_warn <= warns)]


def _ratio(ok: int, n: int) -> float | None:
    return round(ok / n, 4) if n else None


def summarize(docs: list[DocResult]) -> dict[str, Any]:
    def block(sel: list[DocResult]) -> dict[str, Any]:
        per_field: dict[str, list[bool]] = defaultdict(list)
        for d in sel:
            for f in d.fields:
                per_field[f.path].append(f.correct)
        gate = [c for d in sel for f in d.fields if f.path in GATE_HEADER_FIELDS for c in [f.correct]]
        tp, pred, tru = sum(d.line_tp for d in sel), sum(d.line_pred for d in sel), sum(d.line_truth for d in sel)
        prec, rec = _ratio(tp, pred), _ratio(tp, tru)
        f1 = round(2 * prec * rec / (prec + rec), 4) if prec and rec else 0.0
        durations = sorted(d.duration_ms for d in sel if d.duration_ms)
        return {
            "documents": len(sel),
            "reached_validated": sum(d.status in VALIDATED for d in sel),
            "failed": [d.name for d in sel if d.status not in VALIDATED],
            "header_accuracy": _ratio(sum(gate), len(gate)),
            "line_precision": prec, "line_recall": rec, "line_f1": f1,
            "line_values_not_on_invoice": sum(d.line_invented for d in sel),
            "fields": {k: _ratio(sum(v), len(v)) for k, v in sorted(per_field.items())},
            "extract_ms_median": durations[len(durations) // 2] if durations else None,
        }

    out = {"all": block(docs)}
    for tier in sorted({d.tier for d in docs}):
        out[tier] = block([d for d in docs if d.tier == tier])
    out["gates"] = {k: {"threshold": v, "value": out["all"].get(k),
                        "pass": (out["all"].get(k) or 0) >= v} for k, v in GATES.items()}
    return out
