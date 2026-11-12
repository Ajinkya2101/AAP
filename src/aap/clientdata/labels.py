"""Training labels for adapter A (IMPLEMENTATION-PLAN-CLIENT-DATA P6): the train split read by the model,
then compared with what AP keyed, so a person checks only what differs.

    python -m aap.clientdata.labels upload  --goldset test-data-kk/<gold set> [--split train] [--inline]
    python -m aap.clientdata.labels compare --goldset test-data-kk/<gold set> [--split train] [--out <dir>]

`upload` takes the split's invoice files in with source `kk-train` (not `eval`: the fine-tuning export
leaves eval documents out). The pipeline runs as for any upload (the worker; `--inline` runs it in this
process). A file already uploaded is not taken twice.

`compare` (P6.1) puts each `kk-train` document's reading next to its keyed record (the gold set's truth):

- compared, because the keyed record has them: invoice number, invoice date, currency, total, tax, and
  the amounts of the non-tax lines (as a multiset: AP's line order is not the page's);
- always to check, because the keyed record does not have them: supplier name and address as printed,
  store, terms as printed, PO number;
- an invoice whose compared fields all agree and whose lines match is **accepted** as a whole (the
  export may use it as an "approved" label once a person approves it); every other one is listed for
  review with what differs and what to check. Corrections go through the invoice page (aap.submit.correct),
  which the export writes as "corrected" examples.

Writes label-review.md and label-review.json into `--out` (default: the gold set; client data, outside git).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

SOURCE = "kk-train"
TAX_TYPES = {"TAX", "NONREC_TAX", "REC_TAX", "AWT"}
TO_CHECK = ("vendor_name", "vendor_address", "location_ref", "terms_name", "po_number")


@dataclass
class LabelResult:
    document_id: str
    invoice_id: int | None
    split: str | None
    status: str                                   # accepted | review | not read
    differences: list[dict[str, Any]] = field(default_factory=list)
    to_check: list[str] = field(default_factory=list)
    lines_match: bool | None = None


def _dec(v: Any) -> Decimal | None:
    try:
        return None if v in (None, "") else Decimal(str(v)).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _alnum(v: Any) -> str | None:
    s = re.sub(r"[^0-9A-Za-z]", "", str(v or "")).upper()
    return s or None


def compare_invoice(inv: dict[str, Any] | None, truth: dict[str, Any]) -> tuple[list[dict[str, Any]], bool | None]:
    """(differences, lines match) between a canonical invoice (JSON) and a gold-set truth. Pure."""
    if inv is None:
        return [{"field": "reading", "model": None, "keyed": "invoice"}], None
    e, src = truth.get("expected") or {}, truth.get("source") or {}

    def val(name: str) -> Any:
        return (inv.get(name) or {}).get("value")

    checks = [("invoice_number", _alnum(val("invoice_num")), _alnum(e.get("invoice_num") or src.get("invoice_number"))),
              ("invoice_date", val("invoice_date"), e.get("invoice_date")),
              ("currency", val("currency_code"), e.get("currency_code")),
              ("total", _dec(val("invoice_amount")), _dec(e.get("invoice_amount") or src.get("total")))]
    if "tax_amount" in e:
        checks.append(("tax_amount", _dec(val("tax_amount")) or Decimal("0.00"), _dec(e["tax_amount"])))
    diffs = [{"field": n, "model": None if m is None else str(m), "keyed": None if k is None else str(k)}
             for n, m, k in checks if k is not None and m != k]
    keyed = sorted(_dec(ln.get("amount")) or Decimal(0) for ln in e.get("lines") or []
                   if str(ln.get("line_type") or "ITEM").upper() not in TAX_TYPES)
    read = sorted(_dec((ln.get("amount") or {}).get("value")) or Decimal(0) for ln in inv.get("lines") or []
                  if str(ln.get("line_type") or "ITEM").upper() not in TAX_TYPES)
    return diffs, (keyed == read) if keyed else None


def _truths(goldset: Path) -> dict[str, dict[str, Any]]:
    return {p.name.split(".")[0]: json.loads(p.read_text(encoding="utf-8")) for p in (goldset / "truth").glob("*.truth.json")}


def compare(goldset: Path, *, split: str | None = "train") -> list[LabelResult]:
    from sqlalchemy import select

    from aap.db.models import Document
    from aap.db.session import session_scope
    from aap.pipeline.stages import latest_version

    truths = _truths(goldset)
    out = []
    with session_scope() as s:
        docs = s.scalars(select(Document).where(Document.source == SOURCE, Document.sha256.in_(list(truths)))
                         .order_by(Document.created_at))
        seen: set[str] = set()
        for doc in docs:
            truth = truths[doc.sha256]
            if doc.sha256 in seen or (split and truth.get("split") != split):
                continue
            seen.add(doc.sha256)
            version = latest_version(s, doc.id, ["mapped", "user"])
            inv = version.canonical_invoice if version else None
            diffs, lines = compare_invoice(inv, truth)
            to_check = [f for f in TO_CHECK if inv is not None and (inv.get(f) or {}).get("value") not in (None, "")]
            accepted = inv is not None and not diffs and lines is not False
            out.append(LabelResult(document_id=str(doc.id), invoice_id=truth.get("invoice_id"), split=truth.get("split"),
                                   status="accepted" if accepted else "not read" if inv is None else "review",
                                   differences=diffs, to_check=[] if accepted else to_check, lines_match=lines))
    return out


def markdown(results: list[LabelResult], goldset: str) -> str:
    counts = Counter(r.status for r in results)
    fields = Counter(d["field"] for r in results for d in r.differences)
    out = [f"# Label review: {goldset}", "", "Client data: keep with the gold set.", "",
           f"{len(results)} invoices: {counts.get('accepted', 0)} accepted, {counts.get('review', 0)} to review, "
           f"{counts.get('not read', 0)} not read yet.", "",
           "Differences by field: " + (", ".join(f"{k} {v}" for k, v in fields.most_common()) or "none"), "",
           "| Invoice | Document | Status | Differences (model / keyed) | Lines match | Also check |",
           "|---|---|---|---|---|---|"]
    for r in sorted(results, key=lambda r: (r.status != "review", r.invoice_id or 0)):
        diffs = "; ".join(f"{d['field']}: {d['model']} / {d['keyed']}" for d in r.differences) or "-"
        out.append(f"| {r.invoice_id} | {r.document_id} | {r.status} | {diffs} | "
                   f"{'-' if r.lines_match is None else 'yes' if r.lines_match else 'no'} | "
                   f"{', '.join(r.to_check) or '-'} |")
    return "\n".join(out) + "\n"


def upload(goldset: Path, *, split: str | None = "train", inline: bool = False, log=print) -> dict[str, int]:  # noqa: ANN001
    from aap.db.session import session_scope
    from aap.ingest.intake import intake

    manifest = json.loads((goldset / "manifest.json").read_text(encoding="utf-8"))
    counts: Counter = Counter()
    new = []
    for item in manifest["items"]:
        if split and item.get("split") != split:
            continue
        path = goldset / item["file"]
        with session_scope() as s:
            res = intake(s, path.read_bytes(), filename=path.name, source=SOURCE, uploaded_by=f"{SOURCE}:{goldset.name}")
            counts["new" if res.created else "already uploaded"] += 1
            if res.created:
                new.append(res.document.id)
    for doc_id in new:
        if inline:
            from aap.pipeline.stages import run_pipeline_inline

            run_pipeline_inline(doc_id)
        else:
            from aap.worker.tasks import enqueue_stage

            enqueue_stage("parse", doc_id)
    log(f"[labels] {split or 'all'} split of {goldset.name}: {dict(counts)}"
        + ("" if inline else "; the worker reads the new ones"))
    return dict(counts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap.clientdata.labels", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("upload", "compare"):
        p = sub.add_parser(name)
        p.add_argument("--goldset", type=Path, required=True)
        p.add_argument("--split", default="train", help="test, dev, train, or all")
        if name == "upload":
            p.add_argument("--inline", action="store_true", help="run the pipeline here instead of the worker")
        else:
            p.add_argument("--out", type=Path, default=None, help="default: the gold set folder")
    a = ap.parse_args(argv)
    split = None if a.split == "all" else a.split
    if a.cmd == "upload":
        upload(a.goldset, split=split, inline=a.inline)
        return 0
    results = compare(a.goldset, split=split)
    out = a.out or a.goldset
    out.mkdir(parents=True, exist_ok=True)
    (out / "label-review.md").write_text(markdown(results, a.goldset.name), encoding="utf-8")
    (out / "label-review.json").write_text(json.dumps([asdict(r) for r in results], indent=2), encoding="utf-8")
    counts = Counter(r.status for r in results)
    print(f"[labels] {len(results)} invoices: {dict(counts)}; written to {out / 'label-review.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
