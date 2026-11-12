"""Adapter B on a dataset split, without the pipeline (PLAN-MODEL-JSON E5, the learning curve).

    python -m aap.decide.evaluate --dataset test-data-kk/<decide dataset> --split dev
                                  [--model qwen2.5-vl-7b-kk-decide] [--limit 50] [--json out.json]

Every example's stored request (system text, user text, schema) is sent as production sends it, to the
configured backend (LLM_BACKEND: vllm with the given served model name, or the stub, which answers from
the context pack alone: site defaults and the supplier's most used account, the "no training" floor).
The answer is compared with the target (what AP keyed):

- `exact`: share of invoices whose answer equals the target on every header field and line;
- `header`: per field accuracy;
- `lines`: account-or-set accuracy over the lines whose target has one, by count and by amount
  (the printed line amounts); `abstain`: how often a line whose keyed account is not a candidate is
  answered null (the model cannot write it; null sends it to review);
- `basis`: how often the basis matches the target's.

This measures B on keyed records only; the pipeline's measure (with adapter A's reading and the
checks) is `python -m aap.eval --system s2`. Each run appends one line to runs/eval-runs.jsonl
(headline numbers, no supplier values).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from .schema import HEADER_FIELDS


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def _amount(v: Any) -> Decimal:
    try:
        return abs(Decimal(str(v))) if v not in (None, "") else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _line_value(ln: dict[str, Any]) -> tuple[str | None, str | None]:
    return ln.get("account"), ln.get("distribution_set")


def score(examples: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    """examples: (answer, target, printed). Pure; the CLI and the tests use it."""
    header: dict[str, list[bool]] = defaultdict(list)
    exact = 0
    n_lines = n_ok = n_null = n_abstain = basis_n = basis_ok = 0
    amt = amt_ok = Decimal(0)
    for answer, target, printed in examples:
        amounts = {ln.get("line_number"): _amount(ln.get("amount")) for ln in printed.get("lines") or []}
        all_ok = True
        for k in HEADER_FIELDS:
            ok = answer.get(k) == target.get(k)
            header[k].append(ok)
            all_ok &= ok
            if target.get("basis", {}).get(k) is not None:
                basis_n += 1
                basis_ok += (answer.get("basis") or {}).get(k) == target["basis"][k]
        got = {ln.get("line_number"): ln for ln in answer.get("lines") or []}
        for t in target.get("lines") or []:
            a = got.get(t["line_number"]) or {}
            same = _line_value(a) == _line_value(t) and a.get("line_type_lookup_code") == t.get("line_type_lookup_code")
            all_ok &= same
            if _line_value(t) == (None, None):
                n_null += 1
                n_abstain += _line_value(a) == (None, None)
                continue
            n_lines += 1
            n_ok += _line_value(a) == _line_value(t)
            w = amounts.get(t["line_number"], Decimal(0))
            amt += w
            amt_ok += w if _line_value(a) == _line_value(t) else 0
            if t.get("basis") is not None:
                basis_n += 1
                basis_ok += a.get("basis") == t["basis"]
        exact += all_ok
    return {"invoices": len(examples), "exact": _ratio(exact, len(examples)),
            "header": {k: _ratio(sum(v), len(v)) for k, v in header.items()},
            "lines": {"scored": n_lines, "accuracy": _ratio(n_ok, n_lines), "amount_accuracy": _ratio(float(amt_ok), float(amt))},
            "abstain": {"lines": n_null, "answered_null": _ratio(n_abstain, n_null)},
            "basis": _ratio(basis_ok, basis_n)}


def evaluate(dataset: Path, *, split: str = "dev", model: str | None = None, limit: int | None = None,
             log=print) -> dict[str, Any]:  # noqa: ANN001
    from aap.llm import get_llm
    from aap.llm.client import LlmError, LlmRequest

    from .request import parse_user_text

    meta = json.loads((dataset / "dataset.json").read_text(encoding="utf-8"))
    if meta.get("task") != "decide":
        raise ValueError(f"{dataset} is a {meta.get('task')!r} dataset, not decide")
    rows = [json.loads(x) for x in (dataset / f"{split}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    llm = get_llm()
    examples, failed = [], 0
    for i, r in enumerate(rows[:limit], 1):
        system = r["messages"][0]["content"][0]["text"]
        text = r["messages"][1]["content"][0]["text"]
        printed, pack = parse_user_text(text)
        req = LlmRequest(task="decide", system=system, content=[{"type": "text", "text": text}], schema=r["schema"],
                         model=model, max_tokens=1500, context={"printed": printed, "pack": pack})
        try:
            answer = llm.ask(req).data
        except LlmError as exc:
            failed += 1
            log(f"[decide-eval] {i}/{len(rows)} {r['id']}: failed: {exc}")
            answer = {}
        examples.append((answer, json.loads(r["target"]), printed))
    out = score(examples)
    out["failed"] = failed
    return {"dataset": dataset.name, "split": split, "model": model, "metrics": out,
            "dataset_meta": {k: meta.get(k) for k in ("source", "context_pack_version", "prompt_version",
                                                       "schema_version", "counts")},
            "split_sha256": (meta.get("files") or {}).get(f"{split}.jsonl")}


def run_record(result: dict[str, Any], backend: str) -> dict[str, Any]:
    m = result["metrics"]
    return {"kind": "decide-offline", "at": datetime.now(UTC).isoformat(timespec="seconds"),
            "dataset": result["dataset"], "split": result["split"], "split_sha256": result["split_sha256"],
            "backend": backend, "model": result["model"], **{k: result["dataset_meta"].get(k) for k in
                                                              ("context_pack_version", "prompt_version")},
            "invoices": m["invoices"], "exact": m["exact"], "line_accuracy": m["lines"]["accuracy"],
            "line_amount_accuracy": m["lines"]["amount_accuracy"], "answered_null": m["abstain"]["answered_null"],
            "header": m["header"], "basis": m["basis"], "failed": m["failed"]}


def dataset_fingerprint(dataset: Path) -> str:
    return hashlib.sha256((dataset / "dataset.json").read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    from aap.config import get_settings
    from aap.eval.harness import log_run

    ap = argparse.ArgumentParser(prog="python -m aap.decide.evaluate", description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--model", default=None, help="served model name (default: DECIDE_MODEL)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--json", type=Path, default=None)
    a = ap.parse_args(argv)
    s = get_settings()
    model = a.model or s.decide_model
    result = evaluate(a.dataset, split=a.split, model=model, limit=a.limit)
    log_run(run_record(result, s.llm_backend))
    m = result["metrics"]
    print(f"adapter B on {a.dataset.name} [{a.split}], {s.llm_backend}:{model}: {m['invoices']} invoices, "
          f"exact {m['exact']}, lines {m['lines']['accuracy']} (amount {m['lines']['amount_accuracy']}), "
          f"null when not a candidate {m['abstain']['answered_null']}, basis {m['basis']}, failed {m['failed']}")
    print("header: " + ", ".join(f"{k} {v}" for k, v in m["header"].items()))
    if a.json:
        a.json.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
