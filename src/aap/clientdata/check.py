"""Check a client data delivery before anything is loaded (IMPLEMENTATION-PLAN-CLIENT-DATA P1).

    python -m aap.clientdata.check client-data/kk/2026-11-10 [--no-write] [--no-pdf]

Reports, as counts with the first offending IDs:
- files: extracts not recognised, attachments not named by INVOICE_ID, tables not delivered;
- shape: required columns, duplicate or empty keys;
- joins: attachment <-> invoice, line -> invoice, distribution -> line and -> code combination,
  invoice / site -> supplier, terms, payment methods, selected suppliers, store cost centers;
- content: invoice amount = sum of lines, line amount = sum of its distributions, cancelled
  invoices, account segment count, invoice types, PO-matched invoices;
- per supplier: invoices, attachments, date range, distinct accounts, scanned (image-only) PDFs.

Writes manifest.json (files, SHA-256, rows per table, result) and check-report.md into the
delivery. Exit code 1 when there are errors: nothing is loaded until they are fixed or understood.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from .readers import Delivery, day, dec, num, read_delivery

Level = Literal["error", "warning", "info"]
CORE_TABLES = ["SELECTED_SUPPLIERS", "AP_SUPPLIERS", "AP_SUPPLIER_SITES_ALL", "GL_CODE_COMBINATIONS",
               "FND_ID_FLEX_SEGMENTS", "FND_FLEX_VALUES", "AP_TERMS", "AP_TERMS_LINES", "IBY_PAYMENT_METHODS",
               "STORES", "AP_INVOICES_ALL", "AP_INVOICE_LINES_ALL", "AP_INVOICE_DISTRIBUTIONS_ALL"]
EXAMPLES = 10
CENT = Decimal("0.01")


@dataclass
class Finding:
    level: Level
    check: str
    message: str
    count: int = 0
    examples: list[str] = field(default_factory=list)


@dataclass
class SupplierSummary:
    vendor_id: int | None
    vendor_num: str | None
    vendor_name: str | None
    invoices: int = 0
    with_attachment: int = 0
    first_invoice: str | None = None
    last_invoice: str | None = None
    lines: int = 0
    distributions: int = 0
    accounts: int = 0
    scanned_pdfs: int = 0


@dataclass
class Report:
    root: str
    findings: list[Finding] = field(default_factory=list)
    tables: dict[str, int] = field(default_factory=dict)
    attachments: int = 0
    suppliers: list[SupplierSummary] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    @property
    def passed(self) -> bool:
        return not self.errors

    def add(self, level: Level, check: str, message: str, items: Iterable[Any] | None = None) -> None:
        """A finding; with `items`, only when there are any (a check that found nothing adds nothing)."""
        found = None if items is None else list(items)
        if found is not None and not found:
            return
        self.findings.append(Finding(level, check, message, len(found or []), [str(i) for i in (found or [])[:EXAMPLES]]))


# ===================================================================== checks
def _shape(d: Delivery, r: Report) -> None:
    for name in CORE_TABLES:
        if not d.has(name):
            r.add("warning", "files", f"{name} not delivered")
    if d.unknown_files:
        r.add("info", "files", "files / sheets that are not a known extract (ignored)", d.unknown_files)
    for level, message, items in d.derived:
        r.add(level, "derived", message, items or None)  # type: ignore[arg-type]
    if d.unnamed_attachments:
        r.add("error", "files", "attachments whose name does not start with the INVOICE_ID", d.unnamed_attachments)
    for name, t in d.tables.items():
        missing = [c for c in t.spec.required if c not in t.columns]
        if missing:
            r.add("error", "columns", f"{name}: required columns missing: {', '.join(missing)}", t.files)
            continue
        keys = [tuple(row.get(k) for k in t.spec.key) for row in t.rows]
        empty = [i + 2 for i, k in enumerate(keys) if any(v is None for v in k)]
        if empty:
            r.add("error", "keys", f"{name}: rows without {'/'.join(t.spec.key)} (row numbers)", empty)
        dupes = [k for k, n in Counter(k for k in keys if all(v is not None for v in k)).items() if n > 1]
        if dupes:
            r.add("error", "keys", f"{name}: duplicate {'/'.join(t.spec.key)}", ["/".join(map(str, k)) for k in dupes])


def _ids(d: Delivery, table: str, column: str) -> set[Any]:
    return {num(row.get(column)) if column.endswith("_ID") else row.get(column) for row in d.rows(table)} - {None}


def _missing(values: Iterable[Any], known: set[Any]) -> list[Any]:
    return sorted({v for v in values if v is not None and v not in known}, key=str)


def _joins(d: Delivery, r: Report) -> None:
    invoices = _ids(d, "AP_INVOICES_ALL", "INVOICE_ID")
    att = Counter(a.invoice_id for a in d.attachments)
    if d.attachments:
        if d.has("AP_INVOICES_ALL"):
            r.add("error", "attachments", "attachments without a keyed invoice (AP_INVOICES_ALL)",
                  _missing(att, invoices))
            r.add("info", "attachments", "keyed invoices without an attachment (history only)",
                  _missing(invoices, set(att)))
        else:
            r.add("error", "attachments", "attachments delivered without AP_INVOICES_ALL: they cannot be scored")
        multi = [f"{i} ({n} files)" for i, n in att.items() if n > 1]
        if multi:
            r.add("info", "attachments", "invoices with several attachment files", multi)
    if d.has("AP_INVOICE_LINES_ALL") and d.has("AP_INVOICES_ALL"):
        r.add("error", "joins", "invoice lines without their invoice",
              _missing((num(x.get("INVOICE_ID")) for x in d.rows("AP_INVOICE_LINES_ALL")), invoices))
    lines = {(num(x.get("INVOICE_ID")), num(x.get("LINE_NUMBER"))) for x in d.rows("AP_INVOICE_LINES_ALL")}
    dists = d.rows("AP_INVOICE_DISTRIBUTIONS_ALL")
    if dists and d.has("AP_INVOICE_LINES_ALL"):
        r.add("error", "joins", "distributions without their invoice line (INVOICE_ID/INVOICE_LINE_NUMBER)",
              _missing(((num(x.get("INVOICE_ID")), num(x.get("INVOICE_LINE_NUMBER"))) for x in dists), lines))
    ccids = _ids(d, "GL_CODE_COMBINATIONS", "CODE_COMBINATION_ID")
    if dists and d.has("GL_CODE_COMBINATIONS"):
        r.add("error", "joins", "distribution accounts (DIST_CODE_COMBINATION_ID) not in GL_CODE_COMBINATIONS",
              _missing((num(x.get("DIST_CODE_COMBINATION_ID")) for x in dists), ccids))
    vendors = _ids(d, "AP_SUPPLIERS", "VENDOR_ID")
    sites = _ids(d, "AP_SUPPLIER_SITES_ALL", "VENDOR_SITE_ID")
    if d.has("AP_SUPPLIERS"):
        r.add("error", "joins", "invoice suppliers (VENDOR_ID) not in AP_SUPPLIERS",
              _missing((num(x.get("VENDOR_ID")) for x in d.rows("AP_INVOICES_ALL")), vendors))
        r.add("error", "joins", "supplier sites whose supplier is not in AP_SUPPLIERS",
              _missing((num(x.get("VENDOR_ID")) for x in d.rows("AP_SUPPLIER_SITES_ALL")), vendors))
    if d.has("AP_SUPPLIER_SITES_ALL"):
        r.add("error", "joins", "invoice sites (VENDOR_SITE_ID) not in AP_SUPPLIER_SITES_ALL",
              _missing((num(x.get("VENDOR_SITE_ID")) for x in d.rows("AP_INVOICES_ALL")), sites))
    if d.has("HR_OPERATING_UNITS"):
        orgs = _ids(d, "HR_OPERATING_UNITS", "ORGANIZATION_ID")
        r.add("warning", "joins", "site / invoice operating units (ORG_ID) not in HR_OPERATING_UNITS",
              _missing([num(x.get("ORG_ID")) for t in ("AP_SUPPLIER_SITES_ALL", "AP_INVOICES_ALL") for x in d.rows(t)],
                       orgs))
    if d.has("AP_TERMS"):
        terms = _ids(d, "AP_TERMS", "TERM_ID")
        r.add("warning", "joins", "TERMS_ID of invoices, suppliers or sites not in AP_TERMS",
              _missing([num(x.get("TERMS_ID")) for t in ("AP_INVOICES_ALL", "AP_SUPPLIERS", "AP_SUPPLIER_SITES_ALL")
                        for x in d.rows(t)], terms))
        r.add("error", "joins", "AP_TERMS_LINES rows whose term is not in AP_TERMS",
              _missing((num(x.get("TERM_ID")) for x in d.rows("AP_TERMS_LINES")), terms))
        if d.has("AP_TERMS_LINES"):
            r.add("warning", "joins", "terms without AP_TERMS_LINES (days / discount unknown)",
                  _missing(terms, _ids(d, "AP_TERMS_LINES", "TERM_ID")))
    if d.has("IBY_PAYMENT_METHODS"):
        methods = _ids(d, "IBY_PAYMENT_METHODS", "PAYMENT_METHOD_CODE")
        used = [x.get("PAYMENT_METHOD_CODE") for x in d.rows("AP_INVOICES_ALL")]
        used += [x.get("PAYMENT_METHOD_LOOKUP_CODE") for t in ("AP_SUPPLIERS", "AP_SUPPLIER_SITES_ALL")
                 for x in d.rows(t)]
        r.add("warning", "joins", "payment methods used but not in IBY_PAYMENT_METHODS", _missing(used, methods))
    if d.has("SELECTED_SUPPLIERS") and d.has("AP_SUPPLIERS"):
        numbers = {str(x.get("SEGMENT1")) for x in d.rows("AP_SUPPLIERS")}
        chosen = {str(num(x.get("SEGMENT1")) or x.get("SEGMENT1")) for x in d.rows("SELECTED_SUPPLIERS")}
        r.add("error", "joins", "selected suppliers not in AP_SUPPLIERS", _missing(chosen, numbers))
        by_id = {num(x.get("VENDOR_ID")): str(x.get("SEGMENT1")) for x in d.rows("AP_SUPPLIERS")}
        r.add("info", "joins", "invoices of suppliers that are not in the selected list",
              sorted({num(x.get("INVOICE_ID")) for x in d.rows("AP_INVOICES_ALL")
                      if by_id.get(num(x.get("VENDOR_ID"))) not in chosen}, key=str))
    used_sets = sorted({num(x.get("DISTRIBUTION_SET_ID")) for x in d.rows("AP_INVOICE_LINES_ALL")} - {None}, key=str)
    if used_sets and d.has("AP_DISTRIBUTION_SETS_ALL"):
        r.add("warning", "joins", "distribution sets used by invoice lines but not in AP_DISTRIBUTION_SETS_ALL",
              _missing(used_sets, _ids(d, "AP_DISTRIBUTION_SETS_ALL", "DISTRIBUTION_SET_ID")))
    elif used_sets:
        r.add("info", "joins", "distribution sets used by invoice lines (their setup not delivered)", used_sets)
    if d.has("AP_DISTRIBUTION_SET_LINES_ALL"):
        r.add("error", "joins", "distribution set lines whose set is not in AP_DISTRIBUTION_SETS_ALL",
              _missing((num(x.get("DISTRIBUTION_SET_ID")) for x in d.rows("AP_DISTRIBUTION_SET_LINES_ALL")),
                       _ids(d, "AP_DISTRIBUTION_SETS_ALL", "DISTRIBUTION_SET_ID")))
    if dists and not d.has("GL_CODE_COMBINATIONS"):
        r.add("warning", "joins", "GL_CODE_COMBINATIONS not delivered: the distributions' accounts are bare "
              "CCIDs (the replica uses placeholder accounts)",
              sorted({num(x.get("DIST_CODE_COMBINATION_ID")) for x in dists} - {None}, key=str))
    _segment_values(d, r)


def _segments(row: dict[str, Any]) -> list[str]:
    out = [row.get(f"SEGMENT{i}") for i in range(1, 31)]
    while out and out[-1] is None:
        out.pop()
    return [str(num(v)) if isinstance(v, float) else (v or "") for v in out]


def _segment_values(d: Delivery, r: Report) -> None:
    """Segment values used by the code combinations exist in their value sets (needs both tables);
    store cost centers are values of the cost-center segment."""
    from aap.common.coa import layout_from_settings

    combos = d.rows("GL_CODE_COMBINATIONS")
    if d.has("FND_ID_FLEX_SEGMENTS") and d.has("FND_FLEX_VALUES") and combos:
        values: dict[str, set[str]] = defaultdict(set)
        for v in d.rows("FND_FLEX_VALUES"):
            values[str(v["FLEX_VALUE_SET_NAME"]).upper()].add(str(v["FLEX_VALUE"]))
        for seg in d.rows("FND_ID_FLEX_SEGMENTS"):
            col, vset = str(seg["APPLICATION_COLUMN_NAME"]).upper(), str(seg.get("FLEX_VALUE_SET_NAME") or "").upper()
            if not vset or vset not in values or not col.startswith("SEGMENT"):
                continue
            used = {_segments(c)[int(col[7:]) - 1] for c in combos if len(_segments(c)) >= int(col[7:])}
            r.add("warning", "accounts", f"{col} ({seg['SEGMENT_NAME']}) values not in value set {vset}",
                  _missing(used, values[vset]))
    if d.has("STORES") and combos:
        n = layout_from_settings().cost_center_segment
        used = {s[n - 1] for c in combos if len(s := _segments(c)) >= n}
        r.add("warning", "stores", f"store cost centers not used as segment {n} by any code combination "
              "(GL_COST_CENTER_SEGMENT)", _missing((str(x.get("COST_CENTER")) for x in d.rows("STORES")), used))


def _content(d: Delivery, r: Report) -> None:
    from aap.common.coa import layout_from_settings

    invoices = d.rows("AP_INVOICES_ALL")
    cancelled = {num(x["INVOICE_ID"]) for x in invoices if x.get("CANCELLED_DATE")}
    if cancelled:
        r.add("info", "content", "cancelled invoices (left out of the checks below and of the history)",
              sorted(cancelled))
    bad_dates = [x.get("INVOICE_ID") for x in invoices if day(x.get("INVOICE_DATE")) is None]
    if bad_dates:
        r.add("warning", "content", "invoices whose INVOICE_DATE could not be read", bad_dates)
    kept = [x for x in d.rows("AP_INVOICE_LINES_ALL")
            if (x.get("DISCARDED_FLAG") or "N") != "Y" and (x.get("CANCELLED_FLAG") or "N") != "Y"]
    line_sum: dict[int | None, Decimal] = defaultdict(Decimal)
    for x in kept:
        line_sum[num(x.get("INVOICE_ID"))] += dec(x.get("AMOUNT")) or 0
    if d.has("AP_INVOICE_LINES_ALL"):
        off = [f"{num(x['INVOICE_ID'])}: {dec(x.get('INVOICE_AMOUNT'))} vs lines {line_sum[num(x['INVOICE_ID'])]}"
               for x in invoices if num(x.get("INVOICE_ID")) not in cancelled and num(x.get("INVOICE_ID")) in line_sum
               and abs((dec(x.get("INVOICE_AMOUNT")) or 0) - line_sum[num(x["INVOICE_ID"])]) > CENT]
        r.add("warning", "content", "invoice amount is not the sum of its lines", off)
    dists = d.rows("AP_INVOICE_DISTRIBUTIONS_ALL")
    dist_sum: dict[tuple, Decimal] = defaultdict(Decimal)
    for x in dists:
        dist_sum[(num(x.get("INVOICE_ID")), num(x.get("INVOICE_LINE_NUMBER")))] += dec(x.get("AMOUNT")) or 0
    if dists:
        off = [f"{k[0]} line {k[1]}: {dec(x.get('AMOUNT'))} vs distributions {dist_sum[k]}" for x in kept
               if (k := (num(x.get("INVOICE_ID")), num(x.get("LINE_NUMBER")))) in dist_sum
               and k[0] not in cancelled and abs((dec(x.get("AMOUNT")) or 0) - dist_sum[k]) > CENT]
        r.add("warning", "content", "line amount is not the sum of its distributions", off)
        reversed_ = [x["INVOICE_DISTRIBUTION_ID"] for x in dists if (x.get("REVERSAL_FLAG") or "N") == "Y"]
        if reversed_:
            r.add("info", "content", "reversed distributions (pairs net to zero; left out of the GL history)",
                  reversed_)
    combos = d.rows("GL_CODE_COMBINATIONS")
    if combos:
        counts = Counter(len(_segments(c)) for c in combos)
        expected = layout_from_settings().segment_count
        if set(counts) != {expected}:
            r.add("warning", "accounts", f"code combinations whose segment count is not GL_SEGMENT_COUNT={expected}",
                  [f"{n} segments: {c} combination(s)" for n, c in sorted(counts.items())])
    types = Counter(x.get("INVOICE_TYPE_LOOKUP_CODE") or "(empty)" for x in invoices)
    if types:
        r.add("info", "content", "invoice types", [f"{t}: {n}" for t, n in types.most_common()])
    po = sorted({num(x.get("INVOICE_ID")) for x in d.rows("AP_INVOICE_LINES_ALL") if x.get("PO_HEADER_ID")}
                | {num(x.get("INVOICE_ID")) for x in invoices if x.get("PO_HEADER_ID")}, key=str)
    if po:
        r.add("info", "content", "PO-matched invoices (the December scope is non-PO)", po)


def _scanned(path: Path) -> bool | None:
    """True when no page of a PDF has a text layer; None for a file that is not a readable PDF."""
    if path.suffix.lower() != ".pdf":
        return None
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path))
        try:
            return all(len(pdf[i].get_textpage().get_text_range().strip()) < 20 for i in range(len(pdf)))
        finally:
            pdf.close()
    except Exception:  # noqa: BLE001 - damaged or encrypted file: reported, not fatal
        return None


def _suppliers(d: Delivery, r: Report, pdf_scan: bool) -> None:
    master = {num(x.get("VENDOR_ID")): x for x in d.rows("AP_SUPPLIERS")}
    attached: dict[int | None, list] = defaultdict(list)
    for a in d.attachments:
        attached[a.invoice_id].append(a)
    accounts: dict[int | None, set] = defaultdict(set)
    inv_vendor = {num(x.get("INVOICE_ID")): num(x.get("VENDOR_ID")) for x in d.rows("AP_INVOICES_ALL")}
    lines = Counter(inv_vendor.get(num(x.get("INVOICE_ID"))) for x in d.rows("AP_INVOICE_LINES_ALL"))
    dists: Counter[int | None] = Counter()
    for x in d.rows("AP_INVOICE_DISTRIBUTIONS_ALL"):
        v = inv_vendor.get(num(x.get("INVOICE_ID")))
        dists[v] += 1
        accounts[v].add(num(x.get("DIST_CODE_COMBINATION_ID")))
    by_vendor: dict[int | None, list] = defaultdict(list)
    for x in d.rows("AP_INVOICES_ALL"):
        if not x.get("CANCELLED_DATE"):
            by_vendor[num(x.get("VENDOR_ID"))].append(x)
    unreadable = []
    for vendor_id in sorted(set(by_vendor) | set(master), key=str):
        m = master.get(vendor_id, {})
        invs = by_vendor.get(vendor_id, [])
        dates = sorted(dd for x in invs if (dd := day(x.get("INVOICE_DATE"))))
        s = SupplierSummary(vendor_id=vendor_id, vendor_num=m.get("SEGMENT1"), vendor_name=m.get("VENDOR_NAME"),
                            invoices=len(invs), first_invoice=str(dates[0]) if dates else None,
                            last_invoice=str(dates[-1]) if dates else None, lines=lines[vendor_id],
                            distributions=dists[vendor_id], accounts=len(accounts[vendor_id] - {None}))
        for x in invs:
            files = attached.get(num(x.get("INVOICE_ID"))) or []
            s.with_attachment += bool(files)
            if pdf_scan:
                for a in files:
                    scanned = _scanned(a.path)
                    if scanned is None and a.path.suffix.lower() == ".pdf":
                        unreadable.append(a.path.name)
                    s.scanned_pdfs += bool(scanned)
        r.suppliers.append(s)
    if unreadable:
        r.add("warning", "attachments", "PDF attachments that could not be opened", unreadable)


def check_delivery(root: Path, *, pdf_scan: bool = True) -> tuple[Report, Delivery]:
    d = read_delivery(root)
    r = Report(root=str(root), tables={n: len(t.rows) for n, t in d.tables.items()}, attachments=len(d.attachments))
    _shape(d, r)
    if not r.errors:                # joins on broken tables would only repeat the shape errors
        _joins(d, r)
        _content(d, r)
    _suppliers(d, r, pdf_scan)
    return r, d


# ===================================================================== output
def manifest(r: Report, d: Delivery) -> dict[str, Any]:
    return {"delivery": Path(r.root).name, "checked_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "result": "pass" if r.passed else "fail", "errors": len(r.errors), "warnings": len(r.warnings),
            "tables": {n: {"rows": len(t.rows), "files": t.files} for n, t in sorted(d.tables.items())},
            "attachments": len(d.attachments), "files": dict(sorted(d.files.items()))}


def markdown(r: Report) -> str:
    out = [f"# Check of {Path(r.root).name}", "",
           f"Result: **{'pass' if r.passed else 'fail'}** ({len(r.errors)} errors, {len(r.warnings)} warnings), "
           f"checked {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.", "", "## Tables", "", "| Table | Rows |", "|---|---|"]
    out += [f"| {n} | {c} |" for n, c in sorted(r.tables.items())] + [f"| attachments | {r.attachments} |", ""]
    for level in ("error", "warning", "info"):
        items = [f for f in r.findings if f.level == level]
        if items:
            out += [f"## {level.title()}s", ""]
            for f in items:
                more = f" (first {len(f.examples)} of {f.count})" if f.count > len(f.examples) else ""
                ex = f": {', '.join(f.examples)}{more}" if f.examples else ""
                out.append(f"- **{f.check}** {f.message}{f' - {f.count}' if f.count else ''}{ex}")
            out.append("")
    if r.suppliers:
        out += ["## Per supplier", "", "| Supplier | Name | Invoices | With attachment | From | To | Lines | "
                "Distributions | Accounts | Scanned PDFs |", "|---|---|---|---|---|---|---|---|---|---|"]
        out += [f"| {s.vendor_num or s.vendor_id} | {s.vendor_name or ''} | {s.invoices} | {s.with_attachment} | "
                f"{s.first_invoice or ''} | {s.last_invoice or ''} | {s.lines} | {s.distributions} | {s.accounts} | "
                f"{s.scanned_pdfs} |" for s in r.suppliers]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap.clientdata.check", description=__doc__.split("\n")[0])
    ap.add_argument("delivery", type=Path)
    ap.add_argument("--no-write", action="store_true", help="do not write manifest.json / check-report.md")
    ap.add_argument("--no-pdf", action="store_true", help="skip the scanned-PDF check (faster)")
    ap.add_argument("--json", action="store_true", help="print the findings as JSON")
    a = ap.parse_args(argv)
    if not a.delivery.is_dir():
        print(f"{a.delivery} is not a folder", file=sys.stderr)
        return 2
    r, d = check_delivery(a.delivery, pdf_scan=not a.no_pdf)
    text = markdown(r)
    if not a.no_write:
        (a.delivery / "manifest.json").write_text(json.dumps(manifest(r, d), indent=2) + "\n", encoding="utf-8")
        (a.delivery / "check-report.md").write_text(text, encoding="utf-8")
    print(json.dumps([asdict(f) for f in r.findings], indent=2) if a.json else text)
    return 0 if r.passed else 1


if __name__ == "__main__":
    sys.exit(main())
