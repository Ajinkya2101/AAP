"""Training datasets in the `aap-train-chat/1` contract (docs/PLAN-TRAINING-PIPELINE.md §3).

    python -m aap.finetune.chat extract --eval-set test-data/invoices-dev-55 --out <dir>
    python -m aap.finetune.chat extract --export <aap.finetune.export output> --out <dir>
    python -m aap.finetune.chat decide-smoke --eval-set test-data/invoices-dev-55 --out <dir>

`extract` (adapter A): every example is the extraction request production sends
(aap.extraction.extract.extraction_requests: system prompt, page images, text layer), with the page
images written as files, and the answer in the production schema and key order as the target.
- from a synthetic eval set: the pages are parsed and routed as the pipeline does; the target is the
  truth's printed values;
- from `aap.finetune.export`: the pages as the model saw them; the target is the corrected or
  approved reading.
Image-only (scanned) chunks are left out for now: their answer also carries evidence boxes, which
neither source has (CLIENT-DATA P6.2). Invoices that need several requests are left out too: the
truth is per invoice, not per chunk.

`decide-smoke` (adapter B, plumbing only): the printed values of a synthetic invoice and a made-up
context pack (its supplier and site among others) -> the supplier, site, OU and currency. The real
context pack (PLAN-MODEL-JSON §4) replaces it; `context_pack_version` tells them apart.

The training side (`training/`, aap_train.data) reads only this contract.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CONTRACT = "aap-train-chat/1"
SERVED_MAX_PIXELS = 1003520            # vLLM --mm-processor-kwargs max_pixels (VLLM_MAX_PIXELS, docker-compose.yml)
DECIDE_CONTEXT_VERSION = "smoke/0"
DECIDE_SYSTEM = (
    "You decide the Oracle Payables fields of an invoice. You get the values printed on the invoice and a "
    "context pack with the candidate suppliers and sites from the Oracle reference data. Answer with the "
    "JSON of the schema. Choose only among the candidates; when none fits, answer null for that field. "
    "For every value say where it came from in basis: printed, site_default, history, or chosen.")


@dataclass
class Builder:
    out: Path
    task: str
    source: str
    dev_share: int = 10
    test_share: int = 10
    seed: int = 7
    examples: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: {"train": [], "dev": [], "test": []})
    skipped: Counter = field(default_factory=Counter)
    extra: dict[str, Any] = field(default_factory=dict)

    def split_of(self, key: str) -> str:
        h = int(hashlib.sha256(f"{self.seed}:{key}".encode()).hexdigest(), 16) % 100
        return "dev" if h < self.dev_share else "test" if h < self.dev_share + self.test_share else "train"

    def add(self, example: dict[str, Any], split: str | None = None) -> None:
        split = split or self.split_of(example["id"])
        example["meta"]["split"] = split
        self.examples[split].append(example)

    def image(self, example_id: str, n: int, png: bytes) -> str:
        rel = f"images/{example_id}/{n}.png"
        target = self.out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png)
        return rel

    def write(self) -> dict[str, Any]:
        from aap.extraction.prompts import CURRENT_VERSION

        self.out.mkdir(parents=True, exist_ok=True)
        files = {}
        for split, rows in self.examples.items():
            path = self.out / f"{split}.jsonl"
            with path.open("w", encoding="utf-8", newline="\n") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        meta = {"contract": CONTRACT, "task": self.task, "source": self.source,
                "created": datetime.now(UTC).isoformat(timespec="seconds"),
                "counts": {k: len(v) for k, v in self.examples.items()}, "skipped": dict(self.skipped),
                "prompt_version": CURRENT_VERSION if self.task == "extract" else None,
                "image": {"max_pixels": SERVED_MAX_PIXELS} if self.task == "extract" else None,
                "files": files, **self.extra}
        (self.out / "dataset.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        return meta


# ---------------------------------------------------------------- requests
def messages_from_request(b: Builder, example_id: str, system: str, content: list[dict[str, Any]]) -> list[dict]:
    """OpenAI content parts -> contract messages: data-URI images become files."""
    parts, n = [], 0
    for part in content:
        if part.get("type") == "image_url":
            url = part["image_url"]["url"]
            n += 1
            parts.append({"type": "image", "path": b.image(example_id, n, base64.b64decode(url.split(",", 1)[1]))})
        else:
            parts.append({"type": "text", "text": part["text"]})
    return [{"role": "system", "content": [{"type": "text", "text": system}]}, {"role": "user", "content": parts}]


def target_json(value: dict[str, Any], schema_cls: type) -> str:
    """The answer as the model writes it: every schema key, in the schema's order."""
    return json.dumps(schema_cls.model_validate(value).model_dump(mode="json"), ensure_ascii=False)


def complete(value: dict[str, Any], schema_cls: type) -> dict[str, Any]:
    """Every key of the schema (missing ones null, or [] for lists); unknown keys dropped. Recurses
    into the lines and taxes."""
    from aap.schemas.source import ExtractedLine, ExtractedTax

    nested = {"lines": ExtractedLine, "taxes": ExtractedTax}
    out: dict[str, Any] = {}
    for name, f in schema_cls.model_fields.items():
        v = value.get(name)
        if name in nested:
            out[name] = [complete(x, nested[name]) for x in (v or [])]
        elif v is None and "list" in str(f.annotation):
            out[name] = []
        else:
            out[name] = None if v is None else (str(v) if not isinstance(v, str | list | dict) else v)
    return out


def _example(b: Builder, example_id: str, pages: list[Any], target: dict[str, Any], meta: dict[str, Any],
             split: str | None = None) -> None:
    from aap.extraction.extract import extraction_requests
    from aap.schemas.source import ExtractedInvoiceGrounded

    reqs = extraction_requests(pages, sha256=example_id)
    if len(reqs) > 1:
        b.skipped["several requests"] += 1
        return
    r = reqs[0]
    if r.schema_cls is ExtractedInvoiceGrounded:
        b.skipped["image-only pages (evidence boxes not labelled)"] += 1
        return
    try:
        answer = target_json(complete(target, r.schema_cls), r.schema_cls)
    except ValueError:
        b.skipped["target does not fit the schema"] += 1
        return
    b.add({"id": example_id, "task": "extract",
           "messages": messages_from_request(b, example_id, r.request.system, r.request.content),
           "target": answer, "schema": r.request.schema, "meta": meta}, split)


# ------------------------------------------------------------------ extract
def _pages_of(path: Path) -> list[Any]:
    """A file parsed and routed as stages 3-4 do."""
    from aap.config import get_settings
    from aap.extraction.extract import PageInput
    from aap.ingest.filetypes import detect
    from aap.ingest.parse import parse_document
    from aap.ingest.route import route_page

    data = path.read_bytes()
    file_type = detect(data, path.name)
    threshold = get_settings().text_quality_threshold
    out = []
    for p in parse_document(file_type, data):
        route, _, _ = route_page(p.kind, p.text or "", p.words or [], p.height, threshold)
        out.append(PageInput(page_no=p.page_no, kind=p.kind, route=route, text=p.text or "", words=p.words or [],
                             image_png=p.image_png, width=p.width, height=p.height, image_width=p.image_width,
                             image_height=p.image_height))
    return out


def _eval_documents(eval_set: Path) -> list[dict[str, Any]]:
    manifest = json.loads((eval_set / "manifest.json").read_text(encoding="utf-8"))
    docs = []
    for item in manifest.get("items") or []:
        for d in item.get("documents") or []:
            if d.get("pdf") and d.get("sha256"):
                docs.append(d)
    return docs


def extract_from_eval_set(eval_set: Path, out: Path, *, limit: int | None = None, **kw: Any) -> dict[str, Any]:
    b = Builder(out=out, task="extract", source=f"synthetic:{eval_set.name}", **kw)
    for d in _eval_documents(eval_set)[:limit]:
        truth_path = eval_set / "truth" / f"{d['sha256']}.truth.json"
        if not truth_path.exists():
            b.skipped["no truth file"] += 1
            continue
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        _example(b, d["sha256"], _pages_of(eval_set / d["pdf"]), truth.get("source") or {},
                 {"label": "synthetic", "layout": d.get("layout"), "scanned": bool(d.get("scanned"))})
    return b.write()


def extract_from_export(export: Path, out: Path, **kw: Any) -> dict[str, Any]:
    from aap.extraction.extract import PageInput

    b = Builder(out=out, task="extract", source=f"export:{export.name}", **kw)
    for line in (export / "extraction.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        pages = [PageInput(page_no=p["page_no"], kind="pdf", route=p.get("route") or "image_only",
                           text=p.get("text") or "", words=[],
                           image_png=(export / p["image"]).read_bytes() if p.get("image") else None)
                 for p in row["pages"]]
        _example(b, row["sha256"], pages, row["target"], {"label": row["label"], "document_id": row["document_id"]},
                 row.get("split"))
    return b.write()


# ------------------------------------------------------------- decide smoke
def decide_smoke(eval_set: Path, out: Path, *, distractors: int = 3, limit: int | None = None, **kw: Any
                 ) -> dict[str, Any]:
    b = Builder(out=out, task="decide", source=f"synthetic:{eval_set.name}",
                extra={"context_pack_version": DECIDE_CONTEXT_VERSION}, **kw)
    rows = []
    for d in _eval_documents(eval_set)[:limit]:
        truth_path = eval_set / "truth" / f"{d['sha256']}.truth.json"
        if truth_path.exists():
            truth = json.loads(truth_path.read_text(encoding="utf-8"))
            if truth.get("expected", {}).get("vendor_id"):
                rows.append((d["sha256"], truth))
    sites = sorted({(t["expected"]["vendor_id"], t["expected"].get("vendor_site_id"), t["expected"].get("org_id"),
                     t["source"].get("vendor_name")) for _, t in rows}, key=str)
    rnd = random.Random(kw.get("seed", 7))
    schema = {"type": "object", "additionalProperties": False,
              "required": ["vendor_id", "vendor_site_id", "org_id", "currency_code", "basis"],
              "properties": {"vendor_id": {"type": ["integer", "null"]}, "vendor_site_id": {"type": ["integer", "null"]},
                             "org_id": {"type": ["integer", "null"]}, "currency_code": {"type": ["string", "null"]},
                             "basis": {"type": "object"}}}
    for sha, truth in rows:
        exp, src = truth["expected"], truth.get("source") or {}
        own = (exp["vendor_id"], exp.get("vendor_site_id"), exp.get("org_id"), src.get("vendor_name"))
        others = [s for s in sites if s[0] != own[0]]
        candidates = [own, *rnd.sample(others, min(distractors, len(others)))]
        rnd.shuffle(candidates)
        context = {"context_pack_version": DECIDE_CONTEXT_VERSION,
                   "candidates": [{"vendor_id": v, "vendor_site_id": s, "org_id": o, "supplier_name": n}
                                  for v, s, o, n in candidates],
                   "currencies": ["USD", "CAD", "EUR", "MXN"]}
        printed = {k: src.get(k) for k in ("vendor_name", "vendor_address", "vendor_tax_id", "vendor_email",
                                           "invoice_number", "invoice_date", "currency", "total")}
        target = {"vendor_id": exp["vendor_id"], "vendor_site_id": exp.get("vendor_site_id"),
                  "org_id": exp.get("org_id"), "currency_code": exp.get("currency_code"),
                  "basis": {"vendor_id": "chosen", "vendor_site_id": "chosen", "org_id": "site_default",
                            "currency_code": "printed"}}
        user = ("Printed values:\n" + json.dumps(printed, ensure_ascii=False) + "\n\nContext pack:\n"
                + json.dumps(context, ensure_ascii=False))
        b.add({"id": sha, "task": "decide",
               "messages": [{"role": "system", "content": [{"type": "text", "text": DECIDE_SYSTEM}]},
                            {"role": "user", "content": [{"type": "text", "text": user}]}],
               "target": json.dumps(target, ensure_ascii=False), "schema": schema,
               "meta": {"label": "synthetic", "candidates": len(candidates)}})
    return b.write()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap.finetune.chat", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract", help="adapter A dataset")
    g = e.add_mutually_exclusive_group(required=True)
    g.add_argument("--eval-set", type=Path, help="a synthetic set with truth files (test-data/...)")
    g.add_argument("--export", type=Path, help="the output folder of python -m aap.finetune.export")
    s = sub.add_parser("decide-smoke", help="adapter B plumbing dataset from a synthetic set")
    s.add_argument("--eval-set", type=Path, required=True)
    for p in (e, s):
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--dev-share", type=int, default=10, help="percent")
        p.add_argument("--test-share", type=int, default=10, help="percent")
        p.add_argument("--seed", type=int, default=7)
        p.add_argument("--limit", type=int, default=None)
    a = ap.parse_args(argv)
    if a.out.exists() and any(a.out.iterdir()):
        print(f"{a.out} is not empty: a dataset is never changed after it is written", file=sys.stderr)
        return 2
    kw = {"dev_share": a.dev_share, "test_share": a.test_share, "seed": a.seed}
    if a.cmd == "extract" and a.eval_set:
        meta = extract_from_eval_set(a.eval_set, a.out, limit=a.limit, **kw)
    elif a.cmd == "extract":
        meta = extract_from_export(a.export, a.out, **kw)
    else:
        meta = decide_smoke(a.eval_set, a.out, limit=a.limit, **kw)
    print(f"[chat] {meta['task']} dataset in {a.out}: {meta['counts']}; skipped {meta['skipped'] or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
