"""What the keyed AP history alone says, before any invoice file arrives (PLAN-MODEL-JSON §4, §6).

    python -m aap.clientdata.history client-data/kk/<delivery> [--from <earlier delivery> ...]
                                     [--cutoff 2026-10-01] [--min-share 0.9] [--min-invoices 3] [--no-write]

1. **GL from history.** The distributions keyed before the cut-off (GL date) are the history; the ones
   on or after it are coded from that history and compared with what AP keyed, amount-weighted:
   - the label of a distribution is its line's distribution set when the line used one, else its CCID;
   - the prediction is the site's most used label (by amount), else the supplier's; none without history;
   - it is coded **without review** only when that label has at least `--min-share` of the site's
     history amount over at least `--min-invoices` invoices; the rest would go to review;
   - candidate recall at k: how often the keyed label is among the site's k most used labels, which
     sizes the context pack's account list.
2. **Site defaults vs keyed header values** (terms, pay group, payment method): how often AP kept the
   site's value. A field AP never overrides can come from the site instead of the model.
3. **Shape of the history**: invoice and line types, tax lines from the tax engine, empty line
   descriptions, distribution sets.

Reversed distributions and cancelled invoices are left out. Writes history-report.md into the (last)
delivery; it holds client values, like the delivery itself (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from .readers import Delivery, day, dec, num, read_delivery

TAX_TYPES = {"TAX", "NONREC_TAX", "REC_TAX", "TRV", "TERV", "TIPV"}
RECALL_AT = (1, 3, 5, 15)
ZERO = Decimal(0)


@dataclass
class SupplierGl:
    key: str
    name: str
    test_distributions: int = 0
    test_amount: Decimal = ZERO
    history_invoices: int = 0
    history_labels: int = 0
    top_share: float | None = None
    auto: bool = False
    correct_amount: Decimal = ZERO
    recall5_amount: Decimal = ZERO

    @property
    def accuracy(self) -> float | None:
        return float(self.correct_amount / self.test_amount) if self.test_amount else None


@dataclass
class GlResult:
    cutoff: str
    min_share: float
    min_invoices: int
    history_distributions: int = 0
    test_distributions: int = 0
    test_amount: Decimal = ZERO
    no_history_amount: Decimal = ZERO
    correct_amount: Decimal = ZERO
    auto_amount: Decimal = ZERO
    auto_correct_amount: Decimal = ZERO
    set_amount: Decimal = ZERO
    recall_amount: dict[int, Decimal] = field(default_factory=lambda: dict.fromkeys(RECALL_AT, ZERO))
    suppliers: list[SupplierGl] = field(default_factory=list)

    def share(self, part: Decimal, whole: Decimal | None = None) -> float | None:
        whole = self.test_amount if whole is None else whole
        return float(part / whole) if whole else None


@dataclass
class DefaultsResult:
    fields: dict[str, dict[str, int]] = field(default_factory=dict)     # field -> same / different / no default
    changes: dict[str, list[tuple[str, int]]] = field(default_factory=dict)
    by_supplier: dict[str, dict[str, float]] = field(default_factory=dict)


# ------------------------------------------------------------------ input
def _load(paths: list[Path]) -> Delivery:
    if len(paths) == 1:
        return read_delivery(paths[0])
    from aap.ebs_mock.client_load import merge_deliveries

    return merge_deliveries(paths)


def _names(d: Delivery) -> tuple[dict[int | None, str], dict[int | None, int | None]]:
    """VENDOR_ID -> "number name", and VENDOR_SITE_ID -> VENDOR_ID."""
    names = {num(s.get("VENDOR_ID")): f"{s.get('SEGMENT1') or ''} {s.get('VENDOR_NAME') or ''}".strip()
             for s in d.rows("AP_SUPPLIERS")}
    sites = {num(s.get("VENDOR_SITE_ID")): num(s.get("VENDOR_ID")) for s in d.rows("AP_SUPPLIER_SITES_ALL")}
    return names, sites


def _gl_date(x: dict[str, Any]) -> date | None:
    return day(x.get("GL_DATE")) or day(x.get("INVOICE_DATE"))


def default_cutoff(d: Delivery) -> date | None:
    """The first day of the latest GL month: the last month is coded from the months before it."""
    dates = [g for x in d.rows("AP_INVOICES_ALL") if (g := _gl_date(x))]
    return max(dates).replace(day=1) if dates else None


# --------------------------------------------------------------------- GL
def gl_from_history(d: Delivery, cutoff: date, *, min_share: float = 0.9, min_invoices: int = 3) -> GlResult:
    invoices = {num(x["INVOICE_ID"]): x for x in d.rows("AP_INVOICES_ALL") if not x.get("CANCELLED_DATE")}
    names, _ = _names(d)
    line_set = {(num(x.get("INVOICE_ID")), num(x.get("LINE_NUMBER"))): num(x.get("DISTRIBUTION_SET_ID"))
                for x in d.rows("AP_INVOICE_LINES_ALL")}
    res = GlResult(cutoff=str(cutoff), min_share=min_share, min_invoices=min_invoices)
    site_hist: dict[Any, Counter] = defaultdict(Counter)
    vendor_hist: dict[Any, Counter] = defaultdict(Counter)
    site_invoices: dict[Any, set] = defaultdict(set)
    vendor_invoices: dict[Any, set] = defaultdict(set)
    test: list[tuple[Any, Any, str, Decimal]] = []
    for x in d.rows("AP_INVOICE_DISTRIBUTIONS_ALL"):
        inv = invoices.get(num(x.get("INVOICE_ID")))
        line_type = str(x.get("LINE_TYPE_LOOKUP_CODE") or "ITEM").upper()
        if inv is None or line_type in TAX_TYPES or (x.get("REVERSAL_FLAG") or "N") == "Y":
            continue
        gl_date = _gl_date(inv)
        if gl_date is None:
            continue
        key = (num(x.get("INVOICE_ID")), num(x.get("INVOICE_LINE_NUMBER")))
        dist_set = line_set.get(key)
        label = f"set:{dist_set}" if dist_set else f"ccid:{num(x.get('DIST_CODE_COMBINATION_ID'))}"
        amount = abs(dec(x.get("AMOUNT")) or ZERO)
        site, vendor = num(inv.get("VENDOR_SITE_ID")), num(inv.get("VENDOR_ID"))
        if gl_date < cutoff:
            res.history_distributions += 1
            site_hist[site][label] += amount
            vendor_hist[vendor][label] += amount
            site_invoices[site].add(key[0])
            vendor_invoices[vendor].add(key[0])
        else:
            test.append((site, vendor, label, amount))
    per: dict[Any, SupplierGl] = {}
    for site, vendor, label, amount in test:
        hist, n_inv = (site_hist[site], len(site_invoices[site])) if site_invoices.get(site) else \
            (vendor_hist[vendor], len(vendor_invoices[vendor]))
        s = per.get(vendor)
        if s is None:
            s = per[vendor] = SupplierGl(key=str(vendor), name=names.get(vendor) or f"vendor_id {vendor}",
                                         history_invoices=len(vendor_invoices[vendor]),
                                         history_labels=len(vendor_hist[vendor]))
        s.test_distributions += 1
        s.test_amount += amount
        res.test_distributions += 1
        res.test_amount += amount
        if label.startswith("set:"):
            res.set_amount += amount
        if not hist:
            res.no_history_amount += amount
            continue
        ranked = [lbl for lbl, _ in hist.most_common()]
        top_share = float(hist[ranked[0]] / sum(hist.values()))
        auto = top_share >= min_share and n_inv >= min_invoices
        s.top_share, s.auto = round(top_share, 3), s.auto or auto
        correct = ranked[0] == label
        if correct:
            res.correct_amount += amount
            s.correct_amount += amount
        if auto:
            res.auto_amount += amount
            res.auto_correct_amount += amount if correct else ZERO
        rank = ranked.index(label) + 1 if label in ranked else None
        for k in RECALL_AT:
            if rank is not None and rank <= k:
                res.recall_amount[k] += amount
        if rank is not None and rank <= 5:
            s.recall5_amount += amount
    res.suppliers = sorted(per.values(), key=lambda s: -s.test_amount)
    return res


# --------------------------------------------------------------- defaults
DEFAULT_FIELDS = (("terms", "TERMS_ID", "TERMS_ID"), ("pay group", "PAY_GROUP_LOOKUP_CODE", "PAY_GROUP_LOOKUP_CODE"),
                  ("payment method", "PAYMENT_METHOD_CODE", "PAYMENT_METHOD_LOOKUP_CODE"))


def defaults_vs_keyed(d: Delivery) -> DefaultsResult:
    names, _ = _names(d)
    sites = {num(s.get("VENDOR_SITE_ID")): s for s in d.rows("AP_SUPPLIER_SITES_ALL")}
    suppliers = {num(s.get("VENDOR_ID")): s for s in d.rows("AP_SUPPLIERS")}
    res = DefaultsResult()
    changes: dict[str, Counter] = defaultdict(Counter)
    per: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for label, _, _ in DEFAULT_FIELDS:
        res.fields[label] = {"same": 0, "different": 0, "no default": 0}
    for x in d.rows("AP_INVOICES_ALL"):
        if x.get("CANCELLED_DATE"):
            continue
        site = sites.get(num(x.get("VENDOR_SITE_ID")))
        if site is None:
            continue
        supplier = suppliers.get(num(x.get("VENDOR_ID"))) or {}
        who = names.get(num(x.get("VENDOR_ID"))) or str(num(x.get("VENDOR_ID")))
        for label, inv_col, site_col in DEFAULT_FIELDS:
            default = site.get(site_col) if site.get(site_col) is not None else supplier.get(site_col)
            keyed = x.get(inv_col)
            if default is None or keyed is None:
                res.fields[label]["no default"] += 1
                continue
            same = (num(default) == num(keyed)) if inv_col.endswith("_ID") else str(default) == str(keyed)
            res.fields[label]["same" if same else "different"] += 1
            per[who][label][0] += same
            per[who][label][1] += 1
            if not same:
                changes[label][f"{default} -> {keyed}"] += 1
    res.changes = {k: v.most_common(10) for k, v in changes.items()}
    res.by_supplier = {who: {label: round(s / n, 3) for label, (s, n) in f.items() if n}
                       for who, f in sorted(per.items())}
    return res


# ------------------------------------------------------------------ shape
def history_shape(d: Delivery) -> dict[str, Any]:
    names, _ = _names(d)
    invoices = [x for x in d.rows("AP_INVOICES_ALL") if not x.get("CANCELLED_DATE")]
    lines = d.rows("AP_INVOICE_LINES_ALL")
    non_tax = [x for x in lines if str(x.get("LINE_TYPE_LOOKUP_CODE") or "").upper() not in TAX_TYPES]
    tax = [x for x in lines if str(x.get("LINE_TYPE_LOOKUP_CODE") or "").upper() in TAX_TYPES]
    credit = Counter(names.get(num(x.get("VENDOR_ID"))) or str(num(x.get("VENDOR_ID"))) for x in invoices
                     if str(x.get("INVOICE_TYPE_LOOKUP_CODE") or "").upper() not in ("STANDARD", ""))
    months = Counter(g.strftime("%Y-%m") for x in invoices if (g := _gl_date(x)))
    return {
        "invoices": len(invoices),
        "gl_months": dict(sorted(months.items())),
        "invoice_types": dict(Counter(str(x.get("INVOICE_TYPE_LOOKUP_CODE") or "(empty)") for x in invoices)),
        "non_standard_by_supplier": dict(credit.most_common()),
        "sources": dict(Counter(str(x.get("SOURCE") or "(empty)") for x in invoices)),
        "line_types": dict(Counter(str(x.get("LINE_TYPE_LOOKUP_CODE")) for x in lines)),
        "non_tax_lines_without_description": sum(1 for x in non_tax if not x.get("DESCRIPTION")),
        "non_tax_lines": len(non_tax),
        "tax_lines": len(tax),
        "tax_lines_from_tax_engine": sum(1 for x in tax if str(x.get("LINE_SOURCE") or "").upper() == "ETAX"),
        "tax_lines_non_zero": sum(1 for x in tax if (dec(x.get("AMOUNT")) or ZERO) != 0),
        "tax_regimes": dict(Counter(str(x.get("TAX_REGIME_CODE")) for x in tax if x.get("TAX_REGIME_CODE"))),
        "lines_with_distribution_set": sum(1 for x in non_tax if num(x.get("DISTRIBUTION_SET_ID"))),
        "distribution_sets": dict(Counter(str(num(x.get("DISTRIBUTION_SET_ID"))) for x in non_tax
                                          if num(x.get("DISTRIBUTION_SET_ID")))),
    }


# ----------------------------------------------------------------- output
def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v:.0%}"


def markdown(gl: GlResult, defaults: DefaultsResult, shape: dict[str, Any], delivery: str) -> str:
    out = [f"# History report: {delivery}", "",
           "From the keyed AP records only (no invoice files). Client data: keep with the delivery.", "",
           "## 1. GL coding from history", "",
           f"History: GL date before {gl.cutoff} ({gl.history_distributions} distributions). Coded: on or after it "
           f"({gl.test_distributions} distributions). Without review when the site's top label has "
           f">= {gl.min_share:.0%} of its history amount over >= {gl.min_invoices} invoices.", "",
           "| Measure (amount-weighted) | Value |", "|---|---|",
           f"| Top label = keyed (all coded) | {_pct(gl.share(gl.correct_amount))} |",
           f"| No history for the supplier | {_pct(gl.share(gl.no_history_amount))} |",
           f"| Coded without review | {_pct(gl.share(gl.auto_amount))} |",
           f"| Accuracy of those without review | {_pct(gl.share(gl.auto_correct_amount, gl.auto_amount))} |",
           f"| Keyed through a distribution set | {_pct(gl.share(gl.set_amount))} |"]
    out += [f"| Keyed label among the site's top {k} | {_pct(gl.share(gl.recall_amount[k]))} |" for k in RECALL_AT]
    out += ["", "| Supplier | Coded | Amount | History invoices | History labels | Top share | Without review | "
            "Top = keyed | In top 5 |", "|---|---|---|---|---|---|---|---|---|"]
    out += [f"| {s.name} | {s.test_distributions} | {s.test_amount:,.2f} | {s.history_invoices} | {s.history_labels} | "
            f"{_pct(s.top_share)} | {'yes' if s.auto else 'no'} | {_pct(s.accuracy)} | "
            f"{_pct(float(s.recall5_amount / s.test_amount) if s.test_amount else None)} |" for s in gl.suppliers]
    out += ["", "## 2. Site defaults vs keyed header values", "", "| Field | Same as site | Different | No default |",
            "|---|---|---|---|"]
    for label, c in defaults.fields.items():
        n = c["same"] + c["different"]
        out.append(f"| {label} | {c['same']} ({_pct(c['same'] / n if n else None)}) | {c['different']} | "
                   f"{c['no default']} |")
    for label, pairs in defaults.changes.items():
        out += ["", f"Most common overrides of the {label} (site default -> keyed): "
                + ", ".join(f"{p} ({n})" for p, n in pairs)]
    out += ["", "| Supplier | " + " | ".join(lbl for lbl, _, _ in DEFAULT_FIELDS) + " |",
            "|---|" + "---|" * len(DEFAULT_FIELDS)]
    out += [f"| {who} | " + " | ".join(_pct(f.get(lbl)) for lbl, _, _ in DEFAULT_FIELDS) + " |"
            for who, f in defaults.by_supplier.items()]
    out += ["", "## 3. Shape of the history", "", "```", json.dumps(shape, indent=2, default=str), "```", ""]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap.clientdata.history", description=__doc__.split("\n")[0])
    ap.add_argument("delivery", type=Path)
    ap.add_argument("--from", dest="earlier", type=Path, action="append", default=[],
                    help="an earlier delivery read together with this one (master data, terms)")
    ap.add_argument("--cutoff", type=date.fromisoformat, default=None, help="default: first day of the last GL month")
    ap.add_argument("--min-share", type=float, default=0.9)
    ap.add_argument("--min-invoices", type=int, default=3)
    ap.add_argument("--no-write", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the results as JSON")
    a = ap.parse_args(argv)
    d = _load([*a.earlier, a.delivery])
    cutoff = a.cutoff or default_cutoff(d)
    if cutoff is None:
        print("no invoices with a GL date in the delivery", file=sys.stderr)
        return 2
    gl = gl_from_history(d, cutoff, min_share=a.min_share, min_invoices=a.min_invoices)
    defaults, shape = defaults_vs_keyed(d), history_shape(d)
    text = markdown(gl, defaults, shape, a.delivery.name)
    if not a.no_write:
        (a.delivery / "history-report.md").write_text(text, encoding="utf-8")
    print(json.dumps({"gl": asdict(gl), "defaults": asdict(defaults), "shape": shape}, indent=2, default=str)
          if a.json else text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
