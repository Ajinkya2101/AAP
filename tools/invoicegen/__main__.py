"""python -m tools.invoicegen --count 500 --out /data/invoicegen [--seed 7] [--no-scenarios]

Writes:
  <out>/pdf/*.pdf              rendered invoices (scanned ones are image-only PDFs)
  <out>/eml/*.eml              emails (S12 two invoices, S15 unknown sender, S14 auto-reply, ~15% random)
  <out>/truth/<sha>.truth.json ground truth per PDF (also copied to TRUTH_DIR unless --no-install-truth)
  <out>/manifest.json          what to deliver, and the expected outcome of each file
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

from aap.config import get_settings
from aap.ebs_mock import scenarios as sc

from .build import Ebs, random_nonpo_docs, random_po_docs, scenario_docs
from .model import InvoiceDoc
from .render import assign_pages, build_email, html_to_pdf, render_html, scan_noise, sha256, write_truth

AP_MAILBOX = "ap@acse.local"


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", s).strip("-")[:80]


def render_doc(doc: InvoiceDoc, rnd: random.Random, out: Path, truth_dirs: list[Path]) -> dict[str, Any]:
    pdf = html_to_pdf(render_html(doc))
    pages = assign_pages(doc, pdf)
    if doc.scanned:
        pdf = scan_noise(pdf, rnd)
    sha = sha256(pdf)
    path = out / "pdf" / f"{doc.key}.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(pdf)
    write_truth(truth_dirs, sha, doc, file_kind="pdf", scanned=doc.scanned, pages=pages)
    return {"pdf": str(path.relative_to(out)).replace("\\", "/"), "sha256": sha, "pages": pages,
            "layout": doc.layout, "scanned": doc.scanned, "expected": doc.expected, "bytes": pdf}


def generate(out: Path, *, count: int, seed: int, scenarios: bool = True, install_truth: bool = True,
             dsn: str | None = None, today: date | None = None, scan_rate: float = 0.10,
             email_rate: float = 0.15) -> dict[str, Any]:
    settings = get_settings()
    rnd = random.Random(seed)
    today = today or date.today()
    out.mkdir(parents=True, exist_ok=True)
    truth_dirs = [out / "truth"] + ([settings.truth_path] if install_truth else [])
    ebs = Ebs(dsn or settings.ebs_ro_dsn)
    used: set[tuple[int, str]] = set()
    docs: list[InvoiceDoc] = []
    try:
        if scenarios:
            docs.extend(scenario_docs(ebs, rnd))
        n_po = round(count * 0.6)
        po_docs = random_po_docs(ebs, rnd, n_po, used, today)
        nonpo_docs = random_nonpo_docs(ebs, rnd, count - len(po_docs), used, today)
        for i, d in enumerate(po_docs + nonpo_docs, start=1):
            d.key = f"R{i:04d}_{d.expected['vendor_id']}_{_safe(d.invoice_number)}"
            d.scanned = rnd.random() < scan_rate
            if rnd.random() < email_rate:
                d.deliver = "eml"
            docs.append(d)
    finally:
        ebs.close()

    items: list[dict[str, Any]] = []
    groups: dict[str, list[tuple[InvoiceDoc, dict[str, Any]]]] = defaultdict(list)
    for doc in docs:
        rendered = render_doc(doc, rnd, out, truth_dirs)
        if doc.deliver == "eml":
            groups[doc.email_group or doc.key].append((doc, rendered))
        else:
            items.append({"kind": "pdf", "file": rendered["pdf"], "documents": [_public(rendered)]})

    for key, members in groups.items():
        first = members[0][0]
        sender = first.sender or first.vendor.email or f"billing@{_safe(first.vendor.name).lower()}.example"
        nums = ", ".join(d.invoice_number for d, _ in members)
        raw = build_email(sender=sender, to=AP_MAILBOX, subject=f"Invoice {nums} from {first.vendor.name}",
                          body=f"Hello,\n\nplease find attached invoice(s) {nums}.\n\nBest regards,\n{first.vendor.name}\n",
                          attachments=[(f"{_safe(d.invoice_number)}.pdf", r["bytes"]) for d, r in members])
        path = out / "eml" / f"{_safe(key)}.eml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        items.append({"kind": "eml", "file": str(path.relative_to(out)).replace("\\", "/"), "sender": sender,
                      "documents": [_public(r) for _, r in members]})

    if scenarios:
        ooo = sc.S14_AUTO_REPLY
        raw = build_email(sender=ooo["sender"], to=AP_MAILBOX, subject=ooo["subject"].format(ref="S3TEST"),
                          body=ooo["body"], attachments=[],
                          headers={"Auto-Submitted": "auto-replied", "X-Autoreply": "yes",
                                   "In-Reply-To": "<aap-s3-exception@acse.local>"})
        path = out / "eml" / "S14_auto_reply.eml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        items.append({"kind": "eml", "file": "eml/S14_auto_reply.eml", "sender": ooo["sender"],
                      "documents": [], "expected": {"scenario": "S14", "status": "NOT_INVOICE"}})

    manifest = {"seed": seed, "count": count, "generated_on": today.isoformat(), "items": items,
                "stats": {"pdf_files": sum(1 for d in docs), "items": len(items),
                          "scanned": sum(1 for d in docs if d.scanned),
                          "layouts": sorted({d.layout for d in docs})}}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return manifest


def _public(rendered: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rendered.items() if k != "bytes"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m tools.invoicegen", description="Generate synthetic invoices")
    p.add_argument("--count", type=int, default=500, help="random invoices in addition to the scenarios")
    p.add_argument("--out", type=Path, default=Path("/data/invoicegen"))
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--no-scenarios", action="store_true")
    p.add_argument("--no-install-truth", action="store_true", help="do not copy sidecars into TRUTH_DIR")
    p.add_argument("--scan-rate", type=float, default=0.10)
    p.add_argument("--email-rate", type=float, default=0.15)
    a = p.parse_args(argv)
    m = generate(a.out, count=a.count, seed=a.seed, scenarios=not a.no_scenarios,
                 install_truth=not a.no_install_truth, scan_rate=a.scan_rate, email_rate=a.email_rate)
    print(json.dumps(m["stats"], indent=1))
    print(f"manifest: {a.out / 'manifest.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
