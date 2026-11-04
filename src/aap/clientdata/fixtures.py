"""A made-up delivery in the layout the real one is asked for (IMPLEMENTATION-PLAN-CLIENT-DATA P1.4).

    python -m aap.clientdata.fixtures <dir> [--broken]

Three suppliers, one operating unit, an 8-segment chart of accounts joined by "." (store / cost
center in segment 3, natural account in segment 4, as assumed for Krispy Kreme), terms with their
lines, payment methods, a store table and 14 non-PO invoices with lines, distributions and PDF
attachments named by INVOICE_ID (one of them image-only, one invoice cancelled, one distribution
reversed and re-keyed). Every name, number and account is invented.

`--broken` adds the mistakes the checker must catch: an attachment without an invoice, a line
without its invoice, a distribution on an unknown account, an invoice that does not add up.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

D = Decimal
ORG = 81
COA = 50410
SUPPLIERS = [  # vendor_id, number, name, site id, site code, terms id, payment method, store-coded?
    (5001, "70001", "Northwind Packaging LLC", 6001, "NWP-MAIN", 102, "EFT"),
    (5002, "70002", "Bluebird Facility Services", 6002, "BB-REMIT", 101, "CHECK"),
    (5003, "70003", "Comercial Azteca SA de CV", 6003, "AZT-MX", 104, "WIRE"),
]
STORES = [("S0110", "00110"), ("S0316", "00316"), ("S0451", "00451")]
NATURAL = {"packaging": ("61210", "Packaging supplies"), "cleaning": ("62150", "Cleaning and janitorial"),
           "repairs": ("62910", "Repairs and maintenance"), "freight": ("65020", "Freight in")}
TERMS = [  # term_id, name, description, lines (due_days, due_day_of_month, months_forward, disc%, disc_days)
    (101, "30 NET", "NET DUE IN 30 DAYS", [(30, None, None, None, None)]),
    (102, "30 NET 2%10", "NET DUE IN 30 DAYS 2% DISCOUNT IN 10 DAYS", [(30, None, None, D(2), 10)]),
    (103, "15 EOM", "NET DUE ON THE 15TH AFTER MONTH END", [(None, 15, 1, None, None)]),
    (104, "30 DIAS", "30 DIAS", [(30, None, None, None, None)]),
    (105, "CONTADO", "CONTADO", [(0, None, None, None, None)]),
]
PAYMENT_METHODS = [("CHECK", "Check"), ("EFT", "Electronic"), ("WIRE", "Wire")]
LINES = {  # supplier -> (description, natural account key, store index or None)
    5001: [("Donut boxes 12 count", "packaging", 0), ("Glaze cups 4 oz", "packaging", 1)],
    5002: [("Monthly floor cleaning", "cleaning", 0), ("Fryer hood service", "repairs", 2)],
    5003: [("Cajas para donas", "packaging", 1), ("Flete", "freight", None)],
}


def account(store: int | None, natural: str) -> str:
    cc = STORES[store][1] if store is not None else "00000"
    return f"10.20.{cc}.{NATURAL[natural][0]}.00000.000.00.000"


# ------------------------------------------------------------------ PDF
def minimal_pdf(lines: list[str]) -> bytes:
    """A one-page PDF; with no text lines it has no text layer (as a scanned invoice)."""
    text = "\n".join(f"BT /F1 11 Tf 50 {780 - 16 * i} Td ({t.replace('(', '[').replace(')', ']')}) Tj ET"
                     for i, t in enumerate(lines))
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            "/Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(text.encode('latin-1'))} >>\nstream\n{text}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


# --------------------------------------------------------------- tables
def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    cols = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows({k: "" if v is None else v for k, v in r.items()} for r in rows)


def build(root: Path, *, broken: bool = False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "attachments").mkdir(exist_ok=True)
    _write(root / "selected_suppliers.csv", [{"VENDOR_NUM": num} for _, num, *_ in SUPPLIERS])
    _write(root / "hr_operating_units.csv", [{"ORGANIZATION_ID": ORG, "NAME": "Example Donuts US OU",
                                             "SET_OF_BOOKS_ID": 3001, "SHORT_CODE": "EDUS"}])
    _write(root / "ap_suppliers.csv", [
        {"VENDOR_ID": v, "SEGMENT1": num, "VENDOR_NAME": name, "ENABLED_FLAG": "Y", "TERMS_ID": terms,
         "INVOICE_CURRENCY_CODE": "MXN" if v == 5003 else "USD", "PAYMENT_METHOD_LOOKUP_CODE": pm,
         "PAY_GROUP_LOOKUP_CODE": "STANDARD"} for v, num, name, _, _, terms, pm in SUPPLIERS])
    _write(root / "ap_supplier_sites_all.csv", [
        {"VENDOR_SITE_ID": site, "VENDOR_ID": v, "VENDOR_SITE_CODE": code, "ORG_ID": ORG, "PAY_SITE_FLAG": "Y",
         "PRIMARY_PAY_SITE_FLAG": "Y", "TERMS_ID": terms, "PAYMENT_METHOD_LOOKUP_CODE": pm,
         "TERMS_DATE_BASIS": "Invoice", "ACCTS_PAY_CODE_COMBINATION_ID": 90000,
         "ADDRESS_LINE1": f"{100 + i} Example Road", "CITY": "Springfield", "COUNTRY": "MX" if v == 5003 else "US"}
        for i, (v, _, _, site, code, terms, pm) in enumerate(SUPPLIERS)])
    combos = {"10.20.00000.21100.00000.000.00.000": 90000}
    for lines in LINES.values():
        for _, nat, store in lines:
            combos.setdefault(account(store, nat), 90001 + len(combos))
    combos.setdefault(account(2, "packaging"), 90001 + len(combos))     # the miskeyed store (reversed below)
    _write(root / "gl_code_combinations.csv", [
        {"CODE_COMBINATION_ID": ccid, "CHART_OF_ACCOUNTS_ID": COA, "ENABLED_FLAG": "Y",
         "DETAIL_POSTING_ALLOWED_FLAG": "Y", "ACCOUNT_TYPE": "L" if ccid == 90000 else "E",
         **{f"SEGMENT{i}": s for i, s in enumerate(acct.split("."), start=1)}} for acct, ccid in combos.items()])
    segs = [("Company", "ED_COMPANY"), ("Division", "ED_DIVISION"), ("Store", "ED_STORE"), ("Account", "ED_ACCOUNT"),
            ("Sub-account", "ED_SUBACCT"), ("Product", "ED_PRODUCT"), ("Intercompany", "ED_IC"),
            ("Future", "ED_FUTURE")]
    _write(root / "fnd_id_flex_segments.csv", [
        {"ID_FLEX_NUM": COA, "APPLICATION_COLUMN_NAME": f"SEGMENT{i}", "SEGMENT_NAME": name, "SEGMENT_NUM": i,
         "FLEX_VALUE_SET_NAME": vset} for i, (name, vset) in enumerate(segs, start=1)])
    values = [("ED_COMPANY", "10", "Example Donuts US"), ("ED_DIVISION", "20", "Retail"),
              *(("ED_STORE", cc, f"Store {store}") for store, cc in STORES), ("ED_STORE", "00000", "No store"),
              *(("ED_ACCOUNT", a, desc) for a, desc in NATURAL.values()), ("ED_ACCOUNT", "21100", "AP liability"),
              ("ED_SUBACCT", "00000", "None"), ("ED_PRODUCT", "000", "None"), ("ED_IC", "00", "None"),
              ("ED_FUTURE", "000", "None")]
    _write(root / "fnd_flex_values.csv", [{"FLEX_VALUE_SET_NAME": s, "FLEX_VALUE": v, "DESCRIPTION": desc,
                                           "ENABLED_FLAG": "Y"} for s, v, desc in values])
    _write(root / "ap_terms.csv", [{"TERM_ID": t, "NAME": n, "DESCRIPTION": desc, "ENABLED_FLAG": "Y"}
                                   for t, n, desc, _ in TERMS])
    _write(root / "ap_terms_lines.csv", [
        {"TERM_ID": t, "SEQUENCE_NUM": seq, "DUE_PERCENT": 100, "DUE_DAYS": dd, "DUE_DAY_OF_MONTH": dom,
         "DUE_MONTHS_FORWARD": fwd, "DISCOUNT_PERCENT": pct, "DISCOUNT_DAYS": disc}
        for t, _, _, lines in TERMS for seq, (dd, dom, fwd, pct, disc) in enumerate(lines, start=1)])
    _write(root / "iby_payment_methods.csv", [{"PAYMENT_METHOD_CODE": c, "PAYMENT_METHOD_NAME": n, "LANGUAGE": lang}
                                              for c, n in PAYMENT_METHODS for lang in ("US", "ESA")])
    _write(root / "stores.csv", [{"STORE": s, "COST_CENTER": cc} for s, cc in STORES])

    invoices, lines_out, dists = [], [], []
    inv_id, dist_id = 7000100, 9100000
    start = date(2025, 1, 6)
    for v, _, _, site, _, terms, pm in SUPPLIERS:
        for k in range(5 if v != 5003 else 4):
            inv_id += 1
            items = LINES[v]
            amounts = [D(150 + 37 * k + 11 * j).quantize(D("0.01")) + D("0.45") for j in range(len(items))]
            invoices.append({"INVOICE_ID": inv_id, "VENDOR_ID": v, "VENDOR_SITE_ID": site, "ORG_ID": ORG,
                             "INVOICE_NUM": f"{v % 1000:03d}-{2400 + k}", "INVOICE_DATE": start + timedelta(days=17 * k),
                             "INVOICE_AMOUNT": sum(amounts), "INVOICE_CURRENCY_CODE": "MXN" if v == 5003 else "USD",
                             "INVOICE_TYPE_LOOKUP_CODE": "STANDARD", "TERMS_ID": terms, "PAYMENT_METHOD_CODE": pm,
                             "SOURCE": "MANUAL INVOICE ENTRY", "GL_DATE": start + timedelta(days=17 * k + 2),
                             "CANCELLED_DATE": "2025-03-01" if (v, k) == (5002, 4) else None})
            for n, ((desc, nat, store), amt) in enumerate(zip(items, amounts, strict=True), start=1):
                lines_out.append({"INVOICE_ID": inv_id, "LINE_NUMBER": n,
                                  "LINE_TYPE_LOOKUP_CODE": "FREIGHT" if nat == "freight" else "ITEM",
                                  "AMOUNT": amt, "DESCRIPTION": desc})
                acct = combos[account(store, nat)]
                if (v, k, n) == (5001, 2, 1):       # keyed to the wrong store, reversed and re-keyed
                    wrong = combos[account(2, nat)]
                    for amount, ccid, flag, parent in ((amt, wrong, "Y", None), (-amt, wrong, "Y", dist_id + 1),
                                                       (amt, acct, None, None)):
                        dist_id += 1
                        dists.append({"INVOICE_DISTRIBUTION_ID": dist_id, "INVOICE_ID": inv_id,
                                      "INVOICE_LINE_NUMBER": n, "DISTRIBUTION_LINE_NUMBER": dist_id % 10,
                                      "LINE_TYPE_LOOKUP_CODE": "ITEM", "AMOUNT": amount, "DIST_CODE_COMBINATION_ID": ccid,
                                      "DESCRIPTION": desc, "REVERSAL_FLAG": flag, "PARENT_REVERSAL_ID": parent})
                    continue
                dist_id += 1
                dists.append({"INVOICE_DISTRIBUTION_ID": dist_id, "INVOICE_ID": inv_id, "INVOICE_LINE_NUMBER": n,
                              "DISTRIBUTION_LINE_NUMBER": 1, "LINE_TYPE_LOOKUP_CODE": lines_out[-1]["LINE_TYPE_LOOKUP_CODE"],
                              "AMOUNT": amt, "DIST_CODE_COMBINATION_ID": acct, "DESCRIPTION": desc})
            if k < 4:                                # the last invoice of each supplier is history only
                text = [] if (v, k) == (5003, 3) else [
                    "INVOICE", f"Invoice no. {invoices[-1]['INVOICE_NUM']}", f"Date {invoices[-1]['INVOICE_DATE']}",
                    *(f"{d_} {a}" for (d_, _, _), a in zip(items, amounts, strict=True)),
                    f"Total {invoices[-1]['INVOICE_AMOUNT']}"]
                (root / "attachments" / f"{inv_id}.pdf").write_bytes(minimal_pdf(text))
    if broken:
        (root / "attachments" / "7999999.pdf").write_bytes(minimal_pdf(["orphan"]))
        lines_out.append({"INVOICE_ID": 7999998, "LINE_NUMBER": 1, "LINE_TYPE_LOOKUP_CODE": "ITEM", "AMOUNT": 1,
                          "DESCRIPTION": "orphan line"})
        dists[0]["DIST_CODE_COMBINATION_ID"] = 99999999
        invoices[0]["INVOICE_AMOUNT"] = invoices[0]["INVOICE_AMOUNT"] + 1
    _write(root / "ap_invoices_all.csv", invoices)
    _write(root / "ap_invoice_lines_all.csv", lines_out)
    _write(root / "ap_invoice_distributions_all.csv", dists)
    return root


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap.clientdata.fixtures", description=__doc__.split("\n")[0])
    ap.add_argument("dir", type=Path)
    ap.add_argument("--broken", action="store_true")
    a = ap.parse_args(argv)
    print(f"made-up delivery written to {build(a.dir, broken=a.broken)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
