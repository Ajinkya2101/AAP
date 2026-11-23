"""Training datasets in the `aap-train-chat/1` contract (docs/PLAN-TRAINING-PIPELINE.md §3).

    python -m aap.finetune.chat extract --eval-set test-data/invoices-dev-55 --out <dir>
    python -m aap.finetune.chat extract --export <aap.finetune.export output> --out <dir>
    python -m aap.finetune.chat decide-smoke --eval-set test-data/invoices-dev-55 --out <dir>
    python -m aap.finetune.chat decide --delivery client-data/kk/<delivery> [--from <earlier>] \
                                      [--goldset test-data-kk/<gold set>] [--holdout 70003] --out <dir> \
                                      [--suppliers 70001,70002] [--file-invoices <attachments-index.csv>] \
                                      [--per-supplier 200 [--synthetic]]

`extract` (adapter A): every example is the extraction request production sends
(aap.extraction.extract.extraction_requests: system prompt, page images, text layer), with the page
images written as files, and the answer in the production schema and key order as the target.
- from a synthetic eval set: the pages are parsed and routed as the pipeline does; the target is the
  truth's printed values;
- from `aap.finetune.export`: the pages as the model saw them; the target is the corrected or
  approved reading.
Image-only (scanned) chunks need evidence boxes in the answer: the export keeps the model's own boxes
on the fields the target keeps (CLIENT-DATA P6.2); a target without them (the synthetic sets) is left
out. Invoices that need several requests are left out too: the truth is per invoice, not per chunk.

`decide-smoke` (adapter B, plumbing only): the printed values of a synthetic invoice and a made-up
context pack (its supplier and site among others) -> the supplier, site, OU and currency. The real
context pack (PLAN-MODEL-JSON §4) replaces it; `context_pack_version` tells them apart.

`decide` (adapter B, PLAN-MODEL-JSON T2): every keyed non-PO invoice of a delivery becomes an example,
built by production's code (aap.decide.request): the printed values (from the keyed record until the
invoice files arrive: `printed_from: keyed`) and the context pack as of the invoice's GL date -> what AP
keyed, with the basis of each value. Rules (PLAN-MODEL-JSON §7):
- split: the gold set's when one is given (`--goldset`, so B's test is the pipeline's test), else its
  rule: held-out suppliers and each supplier's most recent ~15 % -> test, ~10 % of the rest -> dev;
- no context pack ever holds a test or dev invoice (nor the invoice itself, nor anything keyed on or
  after its GL date); a pack that breaks the rule stops the build;
- the train examples are written in a seeded random order, so `data.limit_train=<n>` (training/) takes
  a random subset and the learning curve's subsets are nested (`learning_curve` in dataset.json);
- invoices without distributions are left out (nothing keyed to learn);
- scope (2026-11-23): `--suppliers` limits the set, `--file-invoices` sends the invoices with a real
  file to test, `--per-supplier N` gives every supplier exactly N train examples: real ones above N are
  left out of training (seeded; they stay in the history), and with `--synthetic` the rest are copies of
  the supplier's real train invoices (aap.decide.synth: label `synthetic`, parent in meta, never in dev
  or test, never in any history; a copy's pack leaves its parent out). The made-up rows are written
  to `synthetic/*.csv` with the SYNTHETIC / PARENT_INVOICE_ID / GEN_VERSION markers. Without
  `--synthetic`, the same command gives the real-only control set with the same dev and test.
Client data: write the dataset under test-data-kk/ (git-ignored), never under training/.

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
    if r.schema_cls is ExtractedInvoiceGrounded and "evidence" not in target:
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
        _example(b, row["sha256"], pages, row["target"],
                 {"label": row["label"], "document_id": row["document_id"], "source": row.get("source"),
                  "invoice_id": row.get("invoice_id")}, row.get("split"))
    return b.write()


# ------------------------------------------------------------------ decide
LEARNING_CURVE = (25, 50, 100)


class LeakError(RuntimeError):
    pass


def _goldset_splits(goldset: Path) -> tuple[dict[int, str], set[str]]:
    """invoice_id -> split, and the held-out suppliers, of a gold set (aap.clientdata.goldset)."""
    splits = {}
    for f in (goldset / "truth").glob("*.truth.json"):
        truth = json.loads(f.read_text(encoding="utf-8"))
        if truth.get("invoice_id") is not None:
            splits[int(truth["invoice_id"])] = truth["split"]
    manifest = json.loads((goldset / "manifest.json").read_text(encoding="utf-8"))
    return splits, set(manifest.get("holdout_suppliers") or [])


def file_invoice_ids(spec: str | Path | None) -> set[int]:
    """Invoice IDs with a real file: a CSV with an INVOICE_ID column (attachments-index.csv of a
    delivery), or IDs separated by commas."""
    import csv

    if not spec:
        return set()
    path = Path(spec)
    if path.suffix.lower() == ".csv" and path.exists():
        with path.open(encoding="utf-8-sig", newline="") as f:
            return {int(r["INVOICE_ID"]) for r in csv.DictReader(f) if (r.get("INVOICE_ID") or "").strip()}
    return {int(x) for x in str(spec).split(",") if x.strip()}


def _write_table(path: Path, rows: list[dict[str, Any]]) -> str:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(dict.fromkeys(k for r in rows for k in r))
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    return hashlib.sha256(path.read_bytes()).hexdigest()


def decide_from_delivery(deliveries: list[Path], out: Path, *, goldset: Path | None = None,
                         holdout: set[str] | None = None, coa: Any = None, seed: int = 7, limit: int | None = None,
                         suppliers: set[str] | None = None, file_invoices: set[int] | None = None,
                         per_supplier: int | None = None, synthetic: bool = False,
                         **kw: Any) -> dict[str, Any]:
    """`suppliers`: the supplier numbers in scope (train, dev and test); held-out suppliers are added
    to the scope as test only. `file_invoices`: invoices with a real file, always test. `per_supplier`:
    exactly this many train examples per supplier in scope: real train invoices above it are left out
    of training (they stay in the history), and with `synthetic` the rest is made up by copies of the
    supplier's real train invoices (aap.decide.synth)."""
    from aap.clientdata.goldset import GoldItem, split_items
    from aap.clientdata.readers import day, num, read_delivery
    from aap.common.coa import layout_from_settings
    from aap.decide import synth
    from aap.decide.context import CONTEXT_PACK_VERSION, build_context_pack, leaks
    from aap.decide.history import TAX_TYPES, account_resolver, from_delivery
    from aap.decide.request import PROMPT_VERSION, SYSTEM, printed_from_keyed, target_from_keyed, user_text
    from aap.decide.schema import SCHEMA_VERSION, check_answer, decide_schema

    if synthetic and not per_supplier:
        raise ValueError("synthetic examples need per_supplier (how many train examples per supplier)")
    file_invoices = set(file_invoices or ())
    coa = coa or layout_from_settings()
    if len(deliveries) == 1:
        d = read_delivery(deliveries[0])
    else:
        from aap.ebs_mock.client_load import merge_deliveries

        d = merge_deliveries(deliveries)
    store = from_delivery(d, coa)
    # history only from suppliers the delivery has a supplier row for: the others (another OU, a supplier
    # the list missed) have no site and often no chart of accounts, and would reach the cross-supplier
    # fallback accounts as placeholders
    orphan = len(store.lines)
    store.lines = [ln for ln in store.lines if ln.vendor_id in store.suppliers]
    orphan -= len(store.lines)
    account_of = account_resolver(d, coa)
    terms = {num(x.get("TERM_ID")): str(x.get("NAME")) for x in d.rows("AP_TERMS")}
    lines: dict[int | None, list[dict[str, Any]]] = {}
    for x in d.rows("AP_INVOICE_LINES_ALL"):
        if (x.get("DISCARDED_FLAG") or "N") != "Y" and (x.get("CANCELLED_FLAG") or "N") != "Y":
            lines.setdefault(num(x.get("INVOICE_ID")), []).append(x)
    dists: dict[int | None, list[dict[str, Any]]] = {}
    for x in d.rows("AP_INVOICE_DISTRIBUTIONS_ALL"):
        dists.setdefault(num(x.get("INVOICE_ID")), []).append(x)

    b = Builder(out=out, task="decide", source=f"keyed:{'+'.join(p.name for p in deliveries)}", seed=seed, **kw)
    if goldset is not None:
        known, held = _goldset_splits(goldset)
        holdout = held if holdout is None else holdout
    holdout = set(holdout or ())
    scope = (set(suppliers) | holdout) if suppliers else None
    candidates: list[tuple[GoldItem, dict[str, Any]]] = []
    for h in d.rows("AP_INVOICES_ALL"):
        inv_id, vendor = num(h.get("INVOICE_ID")), num(h.get("VENDOR_ID"))
        own = lines.get(inv_id) or []
        if h.get("CANCELLED_DATE") or inv_id is None or vendor not in store.suppliers:
            b.skipped["cancelled or supplier not delivered"] += 1
            continue
        if scope is not None and store.suppliers[vendor].vendor_number not in scope:
            b.skipped["supplier not in scope"] += 1
            continue
        if any(x.get("PO_HEADER_ID") for x in own) or h.get("PO_HEADER_ID"):
            b.skipped["PO-matched (non-PO scope)"] += 1
            continue
        if not [x for x in own if str(x.get("LINE_TYPE_LOOKUP_CODE") or "ITEM").upper() not in TAX_TYPES]:
            b.skipped["no non-tax line"] += 1
            continue
        if not synth._live(dists.get(inv_id) or []):
            b.skipped["no distributions (nothing keyed to learn)"] += 1
            continue
        candidates.append((GoldItem(invoice_id=inv_id, vendor_id=vendor,
                                    vendor_number=store.suppliers[vendor].vendor_number,
                                    invoice_date=str(day(h.get("INVOICE_DATE")) or ""), pdf=Path()), h))
    if goldset is not None:
        split_items([it for it, _ in candidates if it.invoice_id not in known], holdout=holdout, seed=seed,
                    force_test=file_invoices)
        for it, _ in candidates:
            if it.invoice_id in known:
                it.split = known[it.invoice_id]
    else:
        split_items([it for it, _ in candidates], holdout=holdout, seed=seed, force_test=file_invoices)
    held_out = {it.invoice_id for it, _ in candidates if it.split in ("test", "dev")}

    # the per-supplier cap: a seeded choice of the real train invoices; the others stay in the history
    rnd = random.Random(seed)
    if per_supplier:
        by_vendor: dict[str | None, list[GoldItem]] = {}
        for it, _ in candidates:
            if it.split == "train":
                by_vendor.setdefault(it.vendor_number, []).append(it)
        for its in by_vendor.values():
            if len(its) > per_supplier:
                keep = {i.invoice_id for i in random.Random(f"{seed}:cap:{its[0].vendor_number}").sample(
                    sorted(its, key=lambda i: i.invoice_id), per_supplier)}
                for i in its:
                    if i.invoice_id not in keep:
                        i.split = "unused"
                        b.skipped["real train invoices over the per-supplier cap (kept in the history)"] += 1

    def set_name(sid: int) -> str:
        ds = store.distribution_sets.get(sid)
        return ds.name if ds is not None and ds.name else f"set {sid}"

    nulls: Counter = Counter()

    def example(h: dict[str, Any], own: list[dict[str, Any]], own_dists: list[dict[str, Any]], vendor_id: int,
                exclude: set[int], pack_as_of: Any = None
                ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any] | None:
        """(messages, target, schema, pack) for one invoice, real or synthetic; None without a date.
        `pack_as_of`: the date the pack is built as of, when not the invoice's own (a synthetic copy
        gets its parent's pack; its own date is never earlier)."""
        as_of = pack_as_of or day(h.get("GL_DATE")) or day(h.get("INVOICE_DATE"))
        if as_of is None:
            return None
        site = num(h.get("VENDOR_SITE_ID"))
        pack = build_context_pack(store, vendor_id=vendor_id, vendor_site_id=site, as_of=as_of,
                                  exclude_invoice_ids=exclude)
        problems = leaks(pack, as_of=as_of, invoice_num=str(h.get("INVOICE_NUM")))
        if problems:
            raise LeakError(f"invoice {h.get('INVOICE_ID')}: {problems}")
        printed = printed_from_keyed(h, own, store.suppliers[vendor_id].vendor_name)
        site_facts = store.sites.get(site) if site is not None else None
        liability = account_of(h.get("ACCTS_PAY_CODE_COMBINATION_ID")) or (site_facts.liability_account
                                                                            if site_facts else None)
        target = target_from_keyed(h, own, own_dists, pack, account_of=account_of, set_name=set_name,
                                   terms_name=terms.get(num(h.get("TERMS_ID"))), liability=liability, coa=coa)
        schema = decide_schema(pack, line_numbers=[ln["line_number"] for ln in printed["lines"]], coa=coa)
        bad = check_answer(target, schema)
        if bad:
            raise ValueError(f"invoice {h.get('INVOICE_ID')}: the target leaves the schema: {bad}")
        nulls["lines"] += len(target["lines"])
        nulls["lines without account or set"] += sum(1 for ln in target["lines"]
                                                     if ln["account"] is None and ln["distribution_set"] is None)
        nulls["header values null"] += sum(1 for k, v in target.items() if k not in ("lines", "basis") and v is None)
        messages = [{"role": "system", "content": [{"type": "text", "text": SYSTEM}]},
                    {"role": "user", "content": [{"type": "text", "text": user_text(printed, pack)}]}]
        meta = {"printed_from": "keyed", "as_of": as_of.isoformat(), "history_scope": pack["history_scope"],
                "fallback": pack["fallback"], "lines": len(target["lines"])}
        return {"messages": messages, "meta": meta}, target, schema, pack

    rows: dict[str, list[dict[str, Any]]] = {"train": [], "dev": [], "test": []}
    per: dict[str, Counter] = {}
    real_train: dict[str | None, list[synth.Invoice]] = {}
    for it, h in candidates[:limit]:
        if it.split == "unused":
            continue
        own, own_dists = lines[it.invoice_id], dists.get(it.invoice_id) or []
        built = example(h, own, own_dists, it.vendor_id, held_out | {it.invoice_id})  # type: ignore[arg-type]
        if built is None:
            b.skipped["no GL or invoice date"] += 1
            continue
        body, target, schema, _ = built
        has_file = it.invoice_id in file_invoices
        rows[it.split].append({
            "id": str(it.invoice_id), "task": "decide", "messages": body["messages"],
            "target": json.dumps(target, ensure_ascii=False), "schema": schema,
            "meta": {"label": "keyed", "supplier": it.vendor_number, "holdout": it.vendor_number in holdout,
                     "has_file": has_file, **body["meta"]}})
        c = per.setdefault(str(it.vendor_number), Counter())
        c["train_real" if it.split == "train" else it.split] += 1
        c["test_with_file"] += int(has_file and it.split == "test")
        if it.split == "train":
            real_train.setdefault(it.vendor_number, []).append(synth.Invoice(h, own, own_dists))

    synthetic_rows: dict[str, list[dict[str, Any]]] = {"AP_INVOICES_ALL": [], "AP_INVOICE_LINES_ALL": [],
                                                       "AP_INVOICE_DISTRIBUTIONS_ALL": []}
    window = None
    if synthetic:
        gl_dates = [day(h.get("GL_DATE")) or day(h.get("INVOICE_DATE")) for _, h in candidates]
        window = (min(x for x in gl_dates if x), max(x for x in gl_dates if x))
        taken: dict[Any, set[str]] = {}
        for _, h in candidates:
            taken.setdefault(num(h.get("VENDOR_ID")), set()).add(str(h.get("INVOICE_NUM")))
        next_id, next_dist = synth.SYNTHETIC_ID_BASE, synth.SYNTHETIC_ID_BASE
        for vendor_number in sorted(real_train, key=str):
            parents_all = real_train[vendor_number]
            need = per_supplier - len(parents_all)  # type: ignore[operator]
            pool = synth.description_pool(parents_all)
            vrnd = random.Random(f"{seed}:synth:{vendor_number}")
            made, attempts = 0, 0
            for parent in synth.parents_for(parents_all, need * 3, vrnd):
                if made >= need:
                    break
                attempts += 1
                vendor_id = num(parent.header.get("VENDOR_ID"))
                inv = synth.clone(parent, invoice_id=next_id, first_dist_id=next_dist, rnd=vrnd, window=window,
                                  descriptions=pool, taken_numbers=taken.setdefault(vendor_id, set()))
                next_id += 1
                next_dist += len(inv.dists)
                parent_id = num(parent.header.get("INVOICE_ID"))
                parent_as_of = day(parent.header.get("GL_DATE")) or day(parent.header.get("INVOICE_DATE"))
                built = example(inv.header, inv.lines, inv.dists, vendor_id,  # type: ignore[arg-type]
                                held_out | {parent_id}, pack_as_of=parent_as_of)  # type: ignore[operator]
                if built is None:
                    b.skipped["synthetic: no date"] += 1
                    continue
                body, target, schema, _ = built
                rows["train"].append({
                    "id": f"syn-{inv.header['INVOICE_ID']}", "task": "decide", "messages": body["messages"],
                    "target": json.dumps(target, ensure_ascii=False), "schema": schema,
                    "meta": {"label": "synthetic", "supplier": vendor_number, "holdout": False, "has_file": False,
                             "parent_invoice_id": parent_id, "gen_version": synth.GEN_VERSION, **body["meta"]}})
                synthetic_rows["AP_INVOICES_ALL"].append(inv.header)
                synthetic_rows["AP_INVOICE_LINES_ALL"] += inv.lines
                synthetic_rows["AP_INVOICE_DISTRIBUTIONS_ALL"] += inv.dists
                made += 1
            per.setdefault(str(vendor_number), Counter())["train_synthetic"] += made
            if made < need:
                b.skipped[f"synthetic short of the per-supplier count ({vendor_number})"] += need - made

    rnd.shuffle(rows["train"])          # limit_train = a random, nested subset
    for split, examples in rows.items():
        for ex in examples:
            b.add(ex, split)
    n_train = len(rows["train"])
    tables = {}
    if any(synthetic_rows.values()):
        tables = {f"synthetic/{name}.csv": _write_table(out / "synthetic" / f"{name}.csv", r)
                  for name, r in synthetic_rows.items()}
    names = {str(s.vendor_number): s.vendor_name for s in store.suppliers.values()}
    b.extra.update({"context_pack_version": CONTEXT_PACK_VERSION, "prompt_version": PROMPT_VERSION,
                    "schema_version": SCHEMA_VERSION, "printed_from": "keyed",
                    "split_rule": f"goldset:{goldset.name}" if goldset else "goldset rule (aap.clientdata.goldset)",
                    "holdout_suppliers": len(holdout), "targets": dict(nulls),
                    "history_lines_without_supplier_row": orphan,
                    "scope": {"suppliers": sorted(suppliers or [], key=str), "holdout": sorted(holdout, key=str),
                              "file_invoices": len(file_invoices), "per_supplier": per_supplier,
                              "synthetic": synthetic, "gen_version": synth.GEN_VERSION if synthetic else None,
                              "synthetic_window": [x.isoformat() for x in window] if window else None},
                    "per_supplier": {k: {"name": names.get(k), "holdout": k in holdout, **dict(v)}
                                     for k, v in sorted(per.items())},
                    "labels": dict(Counter(r["meta"]["label"] for s in rows.values() for r in s)),
                    "synthetic_tables": tables,
                    "learning_curve": {f"{p}%": max(1, round(n_train * p / 100)) for p in LEARNING_CURVE} if n_train else {}})
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
    k = sub.add_parser("decide", help="adapter B dataset from a delivery's keyed records (T2)")
    k.add_argument("--delivery", type=Path, required=True)
    k.add_argument("--from", dest="earlier", type=Path, action="append", default=[],
                   help="an earlier delivery read together with this one (master data, terms)")
    k.add_argument("--goldset", type=Path, default=None, help="take the split and held-out suppliers from it")
    k.add_argument("--holdout", default=None, help="supplier numbers to hold out, comma-separated")
    k.add_argument("--suppliers", default=None, help="supplier numbers in scope, comma-separated (default: all)")
    k.add_argument("--file-invoices", default=None,
                   help="invoices with a real file, always test: a CSV with INVOICE_ID (attachments-index.csv) "
                        "or IDs separated by commas")
    k.add_argument("--per-supplier", type=int, default=None,
                   help="train examples per supplier: real ones above it are left out of training")
    k.add_argument("--synthetic", action="store_true",
                   help="make up the rest to --per-supplier with copies of real train invoices (aap.decide.synth)")
    for p in (e, s, k):
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
    elif a.cmd == "decide":
        holdout = {v.strip() for v in a.holdout.split(",") if v.strip()} if a.holdout else None
        scope = {v.strip() for v in a.suppliers.split(",") if v.strip()} if a.suppliers else None
        meta = decide_from_delivery([*a.earlier, a.delivery], a.out, goldset=a.goldset, holdout=holdout,
                                    limit=a.limit, suppliers=scope, file_invoices=file_invoice_ids(a.file_invoices),
                                    per_supplier=a.per_supplier, synthetic=a.synthetic, **kw)
    else:
        meta = decide_smoke(a.eval_set, a.out, limit=a.limit, **kw)
    print(f"[chat] {meta['task']} dataset in {a.out}: {meta['counts']}; skipped {meta['skipped'] or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
