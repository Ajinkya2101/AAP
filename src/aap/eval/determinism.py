"""Determinism check (PLAN-MID-DECEMBER E1, PLAN-REAL-INVOICES §7): does the same file give the same
reading when the model is busy?

    python -m aap.eval.determinism --dir test-data/invoices-dev-55 [--limit 5] [--runs 6] [--concurrency 6]
    python -m aap.eval.determinism --file <invoice.pdf> [...]

Each file is parsed and routed once, as stages 3-4 do; then the extraction (stage 5, the production
requests) runs `--runs` times one at a time, and `--runs` times all at once (`--concurrency` in
parallel, as the worker's llm queue does). At temperature 0 every answer should be the same. vLLM
batches concurrent requests, and a different batch can change the floating-point sums enough to flip a
token, so the two modes are reported apart:

- distinct answers per file, sequential and concurrent;
- the fields that differ, with the values seen.

Exit code 0 when every file gave one answer in both modes. Needs LLM_BACKEND=vllm (the stub is
deterministic by construction); from the host VLLM_BASE_URL=http://localhost:8100/v1.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class FileResult:
    name: str
    sequential: list[str] = field(default_factory=list)      # canonical JSON of each answer
    concurrent: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def distinct(self, mode: str) -> int:
        return len(set(getattr(self, mode)))

    def differing_fields(self) -> dict[str, list[Any]]:
        return differing_fields([json.loads(x) for x in {*self.sequential, *self.concurrent}])


def _flatten(d: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(d, dict):
        out: dict[str, Any] = {}
        for k, v in d.items():
            out.update(_flatten(v, f"{prefix}{k}."))
        return out
    if isinstance(d, list):
        out = {f"{prefix}count": len(d)}
        for i, v in enumerate(d):
            out.update(_flatten(v, f"{prefix}{i}."))
        return out
    return {prefix.rstrip("."): d}


def differing_fields(answers: list[dict[str, Any]]) -> dict[str, list[Any]]:
    """field path -> the distinct values seen, for the fields that are not the same in every answer."""
    flat = [_flatten(a) for a in answers]
    keys = sorted({k for f in flat for k in f})
    out = {}
    for k in keys:
        values = list(dict.fromkeys(json.dumps(f.get(k), sort_keys=True, default=str) for f in flat))
        if len(values) > 1:
            out[k] = [json.loads(v) for v in values]
    return out


def _canonical(out: Any) -> str:
    return json.dumps(out.extracted.model_dump(mode="json"), sort_keys=True)


def check_file(path: Path, *, runs: int, concurrency: int, llm: Any = None) -> FileResult:
    from aap.extraction.extract import extract
    from aap.finetune.chat import _pages_of
    from aap.llm import get_llm

    llm = llm or get_llm()
    pages = _pages_of(path)
    res = FileResult(name=path.name)

    def one(_: int) -> str:
        return _canonical(extract(pages, llm, sha256=None))

    for i in range(runs):
        try:
            res.sequential.append(one(i))
        except Exception as exc:  # noqa: BLE001 - reported, not fatal
            res.errors.append(f"sequential {i}: {exc}")
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(one, i) for i in range(runs)]
        for i, f in enumerate(futures):
            try:
                res.concurrent.append(f.result())
            except Exception as exc:  # noqa: BLE001
                res.errors.append(f"concurrent {i}: {exc}")
    return res


def report(results: list[FileResult]) -> tuple[str, bool]:
    ok = all(r.distinct("sequential") <= 1 and r.distinct("concurrent") <= 1 and len(set(r.sequential) | set(r.concurrent)) <= 1
             and not r.errors for r in results)
    lines = ["| File | Sequential: distinct answers | Concurrent: distinct answers | Same in both | Fields that differ |",
             "|---|---|---|---|---|"]
    fields: Counter = Counter()
    for r in results:
        diff = r.differing_fields()
        fields.update(diff.keys())
        same = len(set(r.sequential) | set(r.concurrent)) <= 1
        lines.append(f"| {r.name} | {r.distinct('sequential')} of {len(r.sequential)} | "
                     f"{r.distinct('concurrent')} of {len(r.concurrent)} | {'yes' if same else 'no'} | "
                     + (", ".join(f"{k} {v}" for k, v in list(diff.items())[:4]) or "-")
                     + (f" (+{len(diff) - 4} more)" if len(diff) > 4 else "") + " |")
        lines += [f"|  | error: {e} |  |  |  |" for e in r.errors]
    stable = sum(1 for r in results if len(set(r.sequential) | set(r.concurrent)) <= 1 and not r.errors)
    lines += ["", f"{stable} of {len(results)} files gave one answer in both modes."]
    if fields:
        lines.append("Fields that varied (files): " + ", ".join(f"{k} {n}" for k, n in fields.most_common(10)))
    return "\n".join(lines), ok


def main(argv: list[str] | None = None) -> int:
    from aap.config import get_settings

    ap = argparse.ArgumentParser(prog="python -m aap.eval.determinism", description=__doc__.split("\n")[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", type=Path, action="append")
    g.add_argument("--dir", type=Path, help="an eval set: its PDFs (manifest order)")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    if get_settings().llm_backend != "vllm":
        print("LLM_BACKEND is not vllm: the stub is deterministic by construction", file=sys.stderr)
    if a.file:
        files = a.file
    else:
        from aap.finetune.chat import _eval_documents

        files = [a.dir / d["pdf"] for d in _eval_documents(a.dir)][: a.limit]
    results = []
    for f in files:
        r = check_file(f, runs=a.runs, concurrency=a.concurrency)
        print(f"[determinism] {f.name}: sequential {r.distinct('sequential')}, concurrent {r.distinct('concurrent')} "
              f"distinct answer(s)" + (f", {len(r.errors)} error(s)" if r.errors else ""), flush=True)
        results.append(r)
    text, ok = report(results)
    print(text)
    if a.json:
        a.json.write_text(json.dumps([{"file": r.name, "sequential": r.distinct("sequential"),
                                       "concurrent": r.distinct("concurrent"), "fields": r.differing_fields(),
                                       "errors": r.errors} for r in results], indent=2, default=str), encoding="utf-8")
    return 0 if ok else 1



if __name__ == "__main__":
    sys.exit(main())
