"""The learning curve of adapter B (PLAN-MODEL-JSON §6, E5): train on 25 %, 50 % and 100 % of the train
split, serve each adapter, score it on dev. If accuracy still rises at 100 %, more history will help.

    python -m aap.decide.curve --dataset test-data-kk/<decide dataset> [--split dev] [--fractions 25,50,100]
                               [--base-model models/Qwen2.5-VL-7B-Instruct] [--dry-run]

Per fraction, in this order (one GPU: vLLM and training never run together, PLAN-TRAINING-PIPELINE §2):

1. `docker compose stop vllm`
2. `training/`: `python -m aap_train.train --config configs/b-qlora.yaml --set data.dataset=<dataset>
   --set data.limit_train=<n> --set name=b-curve-<p>`: the train examples are in a seeded random order,
   so the subsets are nested (aap.finetune.chat decide)
3. `python -m aap_train.package --run <run> --slot decide`
4. vLLM with the adapters (docker-compose.lora.yml), until the adapter's name is served
5. `aap.decide.evaluate` on the split with DECIDE_ADAPTER_MODEL

Two rows without training come first, from the same split: the stub (site defaults and the supplier's
most used account) and the base model with the context pack (S1). The table goes to stdout and to
runs/learning-curve-<dataset>.json; every evaluation also to runs/eval-runs.jsonl. The read slot
(adapter A) must hold an adapter for vLLM to start with the LoRA file (the T3 smoke adapter will do).
`--dry-run` prints the commands only. VLLM_BASE_URL must reach vLLM from where this runs
(http://localhost:8100/v1 from the host).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
TRAINING = ROOT / "training"
COMPOSE_LORA = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.lora.yml", "--profile", "gpu",
                "up", "-d", "--no-deps", "vllm"]
COMPOSE_STOP = ["docker", "compose", "stop", "vllm"]


@dataclass
class Step:
    what: str
    cmd: list[str]
    cwd: Path


@dataclass
class Curve:
    dataset: Path
    split: str = "dev"
    fractions: tuple[int, ...] = (25, 50, 100)
    config: str = "configs/b-qlora.yaml"
    base_model: str | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)

    def sizes(self) -> dict[int, int]:
        meta = json.loads((self.dataset / "dataset.json").read_text(encoding="utf-8"))
        n_train = int(meta["counts"]["train"])
        if not n_train:
            raise SystemExit(f"{self.dataset} has no train examples")
        return {p: max(1, round(n_train * p / 100)) for p in self.fractions}

    def train_steps(self, p: int, n: int) -> list[Step]:
        sets = [f"data.dataset={self.dataset.resolve()}", f"data.limit_train={n}", f"name=b-curve-{p}"]
        if self.base_model:
            sets.append(f"model.base={self.base_model}")
        train = ["uv", "run", "python", "-m", "aap_train.train", "--config", self.config]
        for s in sets:
            train += ["--set", s]
        return [Step("stop vLLM", COMPOSE_STOP, ROOT), Step(f"train on {n} examples ({p} %)", train, TRAINING)]

    def serve_steps(self, run: str) -> list[Step]:
        return [Step("package into the decide slot",
                     ["uv", "run", "python", "-m", "aap_train.package", "--run", run, "--slot", "decide"], TRAINING),
                Step("serve the adapters", COMPOSE_LORA, ROOT)]


def _run(step: Step, log: Callable[[str], None]) -> str:
    log(f"[curve] {step.what}: {' '.join(step.cmd)}  (in {step.cwd.name})")
    proc = subprocess.run(step.cmd, cwd=step.cwd, capture_output=True, text=True, encoding="utf-8",  # noqa: S603
                          errors="replace", env=os.environ.copy())
    if proc.returncode != 0:
        raise SystemExit(f"[curve] {step.what} failed ({proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}")
    return proc.stdout + proc.stderr


def wait_for_model(name: str, timeout_s: int = 900, log: Callable[[str], None] = print) -> None:
    """Until vLLM lists `name` (it loads the base and the adapters for a few minutes)."""
    from openai import OpenAI

    from aap.config import get_settings

    s = get_settings()
    client = OpenAI(base_url=s.vllm_base_url, api_key=s.vllm_api_key, timeout=10, max_retries=0)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            if name in {m.id for m in client.models.list().data}:
                return
        except Exception:  # noqa: BLE001 - not up yet
            pass
        time.sleep(10)
    raise SystemExit(f"[curve] vLLM did not serve {name} within {timeout_s} s ({s.vllm_base_url})")


def _row(label: str, n: int | None, result: dict[str, Any]) -> dict[str, Any]:
    m = result["metrics"]
    return {"system": label, "train_examples": n, "invoices": m["invoices"], "exact": m["exact"],
            "line_accuracy": m["lines"]["accuracy"], "line_amount_accuracy": m["lines"]["amount_accuracy"],
            "answered_null": m["abstain"]["answered_null"], "terms": m["header"].get("terms_name"), "failed": m["failed"]}


def table(rows: list[dict[str, Any]]) -> str:
    out = ["| System | Train examples | Invoices | Exact | Lines | Lines by amount | Null when not a candidate |",
           "|---|---|---|---|---|---|---|"]
    out += [f"| {r['system']} | {r['train_examples'] if r['train_examples'] is not None else '-'} | {r['invoices']} | "
            f"{r['exact']} | {r['line_accuracy']} | {r['line_amount_accuracy']} | {r['answered_null']} |" for r in rows]
    return "\n".join(out)


def run_curve(curve: Curve, *, dry_run: bool = False, log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    from aap.config import get_settings
    from aap.eval.harness import log_run

    from .evaluate import evaluate, run_record

    sizes = curve.sizes()
    if dry_run:
        for p, n in sizes.items():
            for st in [*curve.train_steps(p, n), *curve.serve_steps(f"runs/b-curve-{p}-<time>")]:
                log(f"[curve] {st.what}: {' '.join(st.cmd)}  (in {st.cwd.name})")
            log(f"[curve] evaluate: python -m aap.decide.evaluate --dataset {curve.dataset} --split {curve.split} "
                f"--model {get_settings().decide_adapter_model}")
        return []
    s = get_settings()

    def scored(label: str, n: int | None, model: str, backend: str) -> None:
        os.environ["LLM_BACKEND"] = backend
        get_settings.cache_clear()
        from aap.llm import get_llm

        get_llm.cache_clear()
        result = evaluate(curve.dataset, split=curve.split, model=model, log=log)
        log_run(run_record(result, backend))
        curve.rows.append(_row(label, n, result))
        log(table(curve.rows))

    scored("stub (context only, no model)", None, "stub", "stub")
    wait_for_model(s.extract_model, log=log)
    scored("S1 base model + context", None, s.extract_model, "vllm")
    for p, n in sizes.items():
        out = "".join(_run(st, log) for st in curve.train_steps(p, n))
        m = re.search(r"\[train\] done: (\S+)", out)
        if not m:
            raise SystemExit(f"[curve] could not find the run folder in the training output:\n{out[-2000:]}")
        for st in curve.serve_steps(m.group(1)):
            _run(st, log)
        wait_for_model(s.decide_adapter_model, log=log)
        scored(f"S2 adapter B, {p} %", n, s.decide_adapter_model, "vllm")
    out_file = ROOT / "runs" / f"learning-curve-{curve.dataset.name}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps({"dataset": curve.dataset.name, "split": curve.split, "rows": curve.rows}, indent=2),
                        encoding="utf-8")
    return curve.rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap.decide.curve", description=__doc__.split("\n")[0])
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--fractions", default="25,50,100")
    ap.add_argument("--config", default="configs/b-qlora.yaml", help="relative to training/")
    ap.add_argument("--base-model", default=None, help="model.base override, e.g. models/Qwen2.5-VL-7B-Instruct")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    curve = Curve(dataset=a.dataset, split=a.split, fractions=tuple(int(x) for x in a.fractions.split(",")),
                  config=a.config, base_model=a.base_model)
    rows = run_curve(curve, dry_run=a.dry_run)
    if rows:
        print(table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
