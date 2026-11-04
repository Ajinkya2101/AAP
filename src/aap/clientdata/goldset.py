"""Gold set: each invoice attachment paired with what AP keyed for it (IMPLEMENTATION-PLAN-CLIENT-DATA P4).

    python -m aap.clientdata.goldset client-data/kk/<delivery> [--out test-data-kk/<name>]
                                     [--holdout 70003,70005] [--holdout-count 1] [--seed 7]

Writes the eval dataset format the harness reads (`python -m aap.eval --dir <out> --split test`):

    pdf/<invoice_id>.pdf
    truth/<sha256>.truth.json   expected = the keyed values (mapped level); source = only the printed
                                values the keyed record also gives (number, total, tax); keys not
                                known are left out, so they are not scored
    manifest.json               items with their split (test / dev / train), never changed afterwards
    history_exclude.json        invoice IDs (test + dev) and the cut-off date to leave out of the
                                replica's AP history, so GL coding never sees its own answer

Split: the invoices of the held-out suppliers (never seen in training: the evidence that more suppliers
are a matter of data), plus the most recent ~15 % of every other supplier -> test; ~10 % of the rest,
chosen by a seeded hash -> dev; the rest -> train. Deterministic for the same delivery and seed.
Only non-cancelled invoices with an attachment are in the set; PO-matched ones are kept but marked.
Output goes outside git (test-data-kk/), like the raw data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from aap.common.coa import CoaLayout, layout_from_settings

from .check import _scanned
from .readers import Delivery, day, dec, num, read_delivery

TEST_SHARE = Decimal("0.15")
DEV_SHARE = 10            # percent of the remaining invoices


@dataclass
class GoldItem:
    invoice_id: int
    vendor_id: int | None
    vendor_number: str | None
    invoice_date: str | None
    pdf: Path
    split: str = "train"
    holdout: bool = False


def _account(combo: dict[str, Any] | None, coa: CoaLayout) -> str | None:
    if combo is None:
        return None
    if combo.get("CONCATENATED_SEGMENTS"):
        return str(combo["CONCATENATED_SEGMENTS"])
    parts = [combo.get(f"SEGMENT{i}") for i in range(1, coa.segment_count + 1)]
    return coa.separator.join(str(p) for p in parts) if all(p is not None for p in parts) else None


def _seeded(seed: int, key: str) -> int:
    return int(hashlib.sha256(f"{seed}:{key}".encode()).hexdigest()[:8], 16)


def split_items(items: list[GoldItem], *, holdout: set[str], seed: int) -> None:
    """Assign test / dev / train in place (see the module docstring)."""
    by_vendor: dict[str | None, list[GoldItem]] = defaultdict(list)
    for it in items:
        by_vendor[it.vendor_number].append(it)
    rest = []
    for vendor, its in by_vendor.items():
        if vendor in holdout:
            for it in its:
                it.split, it.holdout = "test", True
            continue
        its.sort(key=lambda i: (i.invoice_date or "", i.invoice_id))
        n_test = int((Decimal(len(its)) * TEST_SHARE).to_integral_value(rounding="ROUND_HALF_UP")) if len(its) > 2 else 0
        for it in its[len(its) - n_test:] if n_test else []:
            it.split = "test"
        rest += its[:len(its) - n_test]
    for it in rest:
        it.split = "dev" if _seeded(seed, str(it.invoice_id)) % 100 < DEV_SHARE else "train"


def pick_holdout(d: Delivery, count: int, seed: int) -> set[str]:
    """`count` suppliers chosen by a seeded hash among those with at least 4 attached invoices."""
    attached = {a.invoice_id for a in d.attachments}
    per: dict[str, int] = defaultdict(int)
    numbers = {num(s.get("VENDOR_ID")): str(s.get("SEGMENT1")) for s in d.rows("AP_SUPPLIERS")}
    for x in d.rows("AP_INVOICES_ALL"):
        if num(x.get("INVOICE_ID")) in attached:
            per[numbers.get(num(x.get("VENDOR_ID")), str(num(x.get("VENDOR_ID"))))] += 1
    eligible = sorted((v for v, n in per.items() if n >= 4), key=lambda v: _seeded(seed, v))
    return set(eligible[:count]) if len(eligible) > count else set()


def build_goldset(delivery: Path, out: Path, *, holdout: set[str] | None = None, holdout_count: int = 1,
                  seed: int = 7, coa: CoaLayout | None = None, pdf_scan: bool = True) -> dict[str, Any]:
    coa = coa or layout_from_settings()
    d = read_delivery(delivery)
    holdout = holdout if holdout is not None else pick_holdout(d, holdout_count, seed)
    suppliers = {num(s.get("VENDOR_ID")): s for s in d.rows("AP_SUPPLIERS")}
    combos = {num(c.get("CODE_COMBINATION_ID")): c for c in d.rows("GL_CODE_COMBINATIONS")}
    lines: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
    for ln in d.rows("AP_INVOICE_LINES_ALL"):
        if (ln.get("DISCARDED_FLAG") or "N") != "Y" and (ln.get("CANCELLED_FLAG") or "N") != "Y":
            lines[num(ln.get("INVOICE_ID"))].append(ln)
    dists: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for x in d.rows("AP_INVOICE_DISTRIBUTIONS_ALL"):
        if (x.get("REVERSAL_FLAG") or "N") != "Y":            # reversal pairs net to zero
            dists[(num(x.get("INVOICE_ID")), num(x.get("INVOICE_LINE_NUMBER")))].append(x)
    files: dict[int, Path] = {}
    for a in sorted(d.attachments, key=lambda a: a.path.name):
        files.setdefault(a.invoice_id, a.path)

    headers = {num(x.get("INVOICE_ID")): x for x in d.rows("AP_INVOICES_ALL") if not x.get("CANCELLED_DATE")}
    items = [GoldItem(invoice_id=i, vendor_id=num(h.get("VENDOR_ID")),
                      vendor_number=str(suppliers.get(num(h.get("VENDOR_ID")), {}).get("SEGMENT1") or "") or None,
                      invoice_date=str(day(h.get("INVOICE_DATE")) or ""), pdf=files[i])
             for i, h in sorted(headers.items(), key=lambda kv: kv[0] or 0) if i in files]
    split_items(items, holdout=holdout, seed=seed)

    if out.exists():
        shutil.rmtree(out)
    (out / "pdf").mkdir(parents=True)
    (out / "truth").mkdir()
    manifest_items = []
    for it in items:
        h = headers[it.invoice_id]
        target = out / "pdf" / f"{it.invoice_id}{it.pdf.suffix.lower()}"
        shutil.copyfile(it.pdf, target)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        inv_lines = sorted(lines.get(it.invoice_id, []), key=lambda x: num(x.get("LINE_NUMBER")) or 0)
        tax = sum((dec(x.get("AMOUNT")) or 0 for x in inv_lines if x.get("LINE_TYPE_LOOKUP_CODE") == "TAX"),
                  Decimal(0))
        tax_amount = dec(h.get("TOTAL_TAX_AMOUNT")) if h.get("TOTAL_TAX_AMOUNT") is not None else tax
        po = any(x.get("PO_HEADER_ID") for x in inv_lines) or bool(h.get("PO_HEADER_ID"))
        expected = {
            "scenario": None, "vendor_id": it.vendor_id, "vendor_site_id": num(h.get("VENDOR_SITE_ID")),
            "org_id": num(h.get("ORG_ID")), "currency_code": h.get("INVOICE_CURRENCY_CODE"),
            "invoice_num": h.get("INVOICE_NUM"), "invoice_date": str(day(h.get("INVOICE_DATE")) or "") or None,
            "invoice_amount": str(dec(h.get("INVOICE_AMOUNT"))), "tax_amount": str(tax_amount) if tax_amount else None,
            "terms_id": num(h.get("TERMS_ID")), "invoice_type": h.get("INVOICE_TYPE_LOOKUP_CODE"),
            "payment_method": h.get("PAYMENT_METHOD_CODE"), "gl_date": str(day(h.get("GL_DATE")) or "") or None,
            "po_matched": po,
            "lines": [{"line_number": num(x.get("LINE_NUMBER")), "line_type": x.get("LINE_TYPE_LOOKUP_CODE"),
                       "amount": str(dec(x.get("AMOUNT"))), "description": x.get("DESCRIPTION"),
                       "distributions": [{"amount": str(dec(y.get("AMOUNT"))),
                                          "account": _account(combos.get(num(y.get("DIST_CODE_COMBINATION_ID"))), coa)}
                                         for y in dists.get((it.invoice_id, num(x.get("LINE_NUMBER"))), [])]}
                      for x in inv_lines],
            "expect_fail": [], "expect_warn": [], "expect_flags": []}
        if expected["tax_amount"] is None:
            del expected["tax_amount"]              # not known whether tax is keyed: not scored
        source = {"invoice_number": h.get("INVOICE_NUM"), "total": str(dec(h.get("INVOICE_AMOUNT")))}
        scanned = _scanned(target) if pdf_scan else None
        truth = {"sha256": sha, "generator": "aap.clientdata.goldset", "layout": "client", "scanned": bool(scanned),
                 "pages": None, "file_kind": target.suffix.lstrip("."), "invoice_id": it.invoice_id,
                 "vendor_number": it.vendor_number, "split": it.split, "supplier_holdout": it.holdout,
                 "source": source, "expected": expected}
        (out / "truth" / f"{sha}.truth.json").write_text(json.dumps(truth, indent=2) + "\n", encoding="utf-8")
        manifest_items.append({"kind": "pdf", "file": f"pdf/{target.name}", "split": it.split,
                               "documents": [{"pdf": f"pdf/{target.name}", "sha256": sha, "layout": "client",
                                              "scanned": bool(scanned), "expected": expected}]})
    counts = {s: sum(1 for i in items if i.split == s) for s in ("test", "dev", "train")}
    held_out = [i for i in items if i.split in ("test", "dev")]
    exclude = {"invoice_ids": sorted(i.invoice_id for i in held_out),
               # from the recent test invoices only: a held-out supplier's history is out entirely anyway
               "history_cutoff": min((i.invoice_date for i in items
                                      if i.split == "test" and not i.holdout and i.invoice_date), default=None),
               "holdout_suppliers": sorted(holdout),
               "rule": "leave out of the AP history: these invoices, every invoice of the held-out suppliers, "
                       "and everything dated on or after the cut-off"}
    manifest = {"generator": "aap.clientdata.goldset", "delivery": delivery.name,
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"), "seed": seed,
                "holdout_suppliers": sorted(holdout), "counts": counts,
                "account_layout": {"segment_count": coa.segment_count, "separator": coa.separator},
                "items": manifest_items}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (out / "history_exclude.json").write_text(json.dumps(exclude, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap.clientdata.goldset", description=__doc__.split("\n")[0])
    ap.add_argument("delivery", type=Path)
    ap.add_argument("--out", type=Path, default=None, help="default: test-data-kk/<delivery name>")
    ap.add_argument("--holdout", default=None, help="supplier numbers to hold out, comma-separated")
    ap.add_argument("--holdout-count", type=int, default=1)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args(argv)
    out = a.out or Path("test-data-kk") / a.delivery.name
    holdout = {v.strip() for v in a.holdout.split(",") if v.strip()} if a.holdout else None
    m = build_goldset(a.delivery, out, holdout=holdout, holdout_count=a.holdout_count, seed=a.seed)
    print(f"gold set {out}: {m['counts']} held-out suppliers {m['holdout_suppliers'] or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
