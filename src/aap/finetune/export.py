"""python -m aap.finetune.export --out <dir> [--no-images] [--include-unlabelled]
                                 [--source kk-train] [--goldset test-data-kk/<gold set>]

Writes the training examples the application has collected so far:

  extraction.jsonl  one invoice per line: the pages the model saw (image file + text layer),
                    what the model read ("model_output") and what is right ("target").
                    label "corrected": a clerk corrected the reading (aap.submit.correct).
                    label "approved":  a clerk approved the invoice without correcting it.
  choices.jsonl     one model choice per line (supplier, site, terms, category, PO line, GL
                    account): the prompt, the candidates, the model's pick and, when a clerk
                    approved the invoice, the value that was posted ("label").
  images/           the page images, <document id>/<page>.png

Only invoices the real model extracted; eval sets (synthetic, with their own truth files) and
stub runs are left out. "approved" is a weaker label than "corrected": the clerk accepted the
invoice, which does not prove that every field was checked.

For the client's train split (IMPLEMENTATION-PLAN-CLIENT-DATA P6.2): `--source kk-train` keeps only the
documents uploaded by `aap.clientdata.labels upload`; `--goldset` adds each invoice's `split` and
`invoice_id` (by file hash), which `aap.finetune.chat extract --export` keeps. Image-only pages: the
model's own evidence boxes are written into the target (`evidence`) for the header fields whose value
the target keeps; a field corrected to another value loses its box (no box is drawn by hand).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aap.common.coa import layout_from_settings
from aap.db.models import AgentDecision, Document, ExtractionRun, InvoiceVersion, Page
from aap.db.session import session_scope
from aap.filestore import FileStore
from aap.schemas.source import HEADER_FIELDS, LINE_FIELDS, TAX_FIELDS
from aap.submit.correct import USER_BACKEND

POSTED = {"APPROVED", "SUBMITTED", "CREATED"}
REAL_BACKEND = "vllm"


def _flat(source_invoice: dict[str, Any]) -> dict[str, Any]:
    """SourceInvoice JSON -> the flat shape the extraction model answers with."""
    out: dict[str, Any] = {name: (source_invoice.get(name) or {}).get("value") for name in HEADER_FIELDS}
    out["taxes"] = [{name: (t.get(name) or {}).get("value") for name in TAX_FIELDS}
                    for t in source_invoice.get("taxes") or []]
    out["lines"] = [{name: (ln.get(name) or {}).get("value") for name in LINE_FIELDS}
                    for ln in source_invoice.get("lines") or []]
    return out


def _documents(session: Session, source: str | None = None) -> list[Document]:
    q = select(Document).where(Document.source != "eval", Document.file_type.not_in(["eml", "msg"]))
    if source:
        q = q.where(Document.source == source)
    return list(session.scalars(q.order_by(Document.created_at)))


def kept_evidence(raw_output: Any, model_output: dict[str, Any], target: dict[str, Any]) -> list[dict[str, Any]] | None:
    """The model's evidence boxes for the header fields whose value the target keeps; None when the
    reading had no evidence (not an image-only request)."""
    chunks = [c.get("output") or {} for c in raw_output or [] if isinstance(c, dict)]
    boxes = [e for c in chunks for e in c.get("evidence") or []]
    if not any("evidence" in c for c in chunks):
        return None
    return [e for e in boxes if e.get("field") in target and target.get(e["field"]) is not None
            and target.get(e["field"]) == model_output.get(e["field"])]


def gold_splits(goldset: Path | None) -> dict[str, dict[str, Any]]:
    """sha256 -> {"split", "invoice_id"} of a gold set (aap.clientdata.goldset)."""
    if goldset is None:
        return {}
    out = {}
    for p in (goldset / "truth").glob("*.truth.json"):
        t = json.loads(p.read_text(encoding="utf-8"))
        out[p.name.split(".")[0]] = {"split": t.get("split"), "invoice_id": t.get("invoice_id")}
    return out


def extraction_examples(session: Session, out: Path | None, store: FileStore, *, source: str | None = None,
                        goldset: Path | None = None) -> list[dict[str, Any]]:
    """`out`: where the page images are copied to (None: do not copy)."""
    examples = []
    gold = gold_splits(goldset)
    for doc in _documents(session, source):
        runs = list(session.scalars(select(ExtractionRun).where(
            ExtractionRun.document_id == doc.id, ExtractionRun.status == "ok").order_by(ExtractionRun.created_at)))
        model_runs = [r for r in runs if r.backend == REAL_BACKEND]
        if not model_runs:
            continue
        model_run, last = model_runs[-1], runs[-1]
        if last.backend == USER_BACKEND:
            label = "corrected"
        elif doc.status in POSTED:
            label = "approved"
        else:
            continue
        pages = []
        for p in session.scalars(select(Page).where(Page.document_id == doc.id).order_by(Page.page_no)):
            image = None
            if p.image_path:
                image = f"images/{doc.id}/{p.page_no}.png"
                if out is not None:
                    target = out / image
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(store.read(p.image_path))
            pages.append({"page_no": p.page_no, "route": p.route, "image": image, "text": p.text})
        changes = [c for r in runs if r.backend == USER_BACKEND for c in (r.raw_output or [{}])[0].get("changes", [])]
        model_output, target = _flat(model_run.source_invoice), _flat(last.source_invoice)
        evidence = kept_evidence(model_run.raw_output, model_output, target)
        if evidence is not None:
            target["evidence"] = evidence
        examples.append({
            "document_id": str(doc.id), "filename": doc.filename, "sha256": doc.sha256, "label": label,
            "source": doc.source, "split": gold.get(doc.sha256, {}).get("split"),
            "invoice_id": gold.get(doc.sha256, {}).get("invoice_id"),
            "model": model_run.model, "prompt_version": model_run.prompt_version, "pages": pages,
            "model_output": model_output, "target": target, "changes": changes})
    return examples


def _posted_label(node: str, line: int | None, model_choice: Any, approved: dict[str, Any] | None) -> tuple[Any, str | None]:
    """What a clerk posted for this choice: (label, where it comes from)."""
    if approved is None:
        return None, None
    ln = next((x for x in approved.get("lines") or [] if x.get("line_number") == line), None)
    if node == "agent.pair_lines" and ln is not None:
        f = ln.get("po_line_number") or {}
        value = f.get("value")
        return (str(value) if value is not None else "none"), "clerk" if f.get("method") == "user" else "approved"
    if node == "agent.gl_code" and ln is not None:
        f = ln.get("dist_account") or {}
        natural = layout_from_settings().natural_account(f.get("value"))
        return natural, "clerk" if f.get("method") == "user" else "approved"
    if node.startswith("map."):
        return model_choice, "approved"     # the review screen cannot change a mapping; approval accepts it
    return None, None


def choice_examples(session: Session, include_unlabelled: bool = False) -> list[dict[str, Any]]:
    examples = []
    docs = {d.id: d for d in _documents(session)}
    approved: dict[Any, dict[str, Any] | None] = {}
    rows = session.scalars(select(AgentDecision).where(AgentDecision.prompt.is_not(None),
                                                       AgentDecision.model.not_like("stub%"))
                           .order_by(AgentDecision.id))
    for d in rows:
        doc = docs.get(d.document_id)
        if doc is None:
            continue
        if doc.id not in approved:
            v = session.scalars(select(InvoiceVersion).where(InvoiceVersion.document_id == doc.id,
                                                             InvoiceVersion.kind == "approved")
                                .order_by(InvoiceVersion.version.desc()).limit(1)).first()
            approved[doc.id] = v.canonical_invoice if v and doc.status in POSTED else None
        choice = (d.chosen or {}).get("choice")
        line = (d.inputs or {}).get("line")
        label, label_source = _posted_label(d.node, line, choice, approved[doc.id])
        if label is None and not include_unlabelled:
            continue
        examples.append({"document_id": str(doc.id), "node": d.node, "line": line, "model": d.model,
                         "prompt": d.prompt, "candidates": d.candidates, "model_choice": choice,
                         "reason": d.reason, "label": label, "label_source": label_source})
    return examples


def export(out: Path, *, images: bool = True, include_unlabelled: bool = False, source: str | None = None,
           goldset: Path | None = None) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    with session_scope() as session:
        extraction = extraction_examples(session, out if images else None, FileStore(), source=source, goldset=goldset)
        choices = choice_examples(session, include_unlabelled)
    for name, rows in (("extraction.jsonl", extraction), ("choices.jsonl", choices)):
        with (out / name).open("w", encoding="utf-8", newline="\n") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    summary = {"extraction": dict(Counter(e["label"] for e in extraction)), "extraction_total": len(extraction),
               "changed_values": sum(len(e["changes"]) for e in extraction),
               "choices": dict(Counter(f"{c['node']}:{c['label_source'] or 'unlabelled'}" for c in choices)),
               "choices_total": len(choices),
               "splits": dict(Counter(str(e["split"]) for e in extraction)),
               "with_evidence": sum(1 for e in extraction if "evidence" in e["target"])}
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m aap.finetune.export")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--no-images", action="store_true", help="do not copy the page images")
    p.add_argument("--include-unlabelled", action="store_true", help="also write choices nobody approved yet")
    p.add_argument("--source", default=None, help="only documents of this intake source (kk-train: the train split)")
    p.add_argument("--goldset", type=Path, default=None, help="add each invoice's split and invoice_id")
    a = p.parse_args(argv)
    s = export(a.out, images=not a.no_images, include_unlabelled=a.include_unlabelled, source=a.source,
               goldset=a.goldset)
    print(f"[export] extraction: {s['extraction_total']} invoices {s['extraction']}, "
          f"{s['changed_values']} corrected values")
    print(f"[export] choices: {s['choices_total']} {s['choices']}")
    print(f"[export] written to {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
