"""Hyperparameter search (HPO) for a run config: a grid or random sweep, trial after trial on the one GPU.

    python -m aap_train.sweep --sweep configs/sweeps/b-screen.yaml [--dry-run] [--max-trials N]
    python -m aap_train.sweep --sweep configs/sweeps/b-screen.yaml --resume runs/sweeps/<sweep folder>
    python -m aap_train.sweep --report runs/sweeps/<sweep folder>

Sweep file (configs/sweeps/*.yaml):

    name: b-screen
    base: ../b-qlora.yaml              # the run config every trial starts from (relative to this file)
    strategy: grid                     # grid: every combination; random: `trials` samples
    trials: 8                          # random only
    seed: 7
    fixed: {train.epochs: 1}           # overrides for every trial (any key of the run config)
    space:
      train.learning_rate: [1.0e-4, 2.0e-4, 3.0e-4]
      lora:                            # a named dimension whose values set several keys together
        - {method.rank: 8, method.alpha: 16}
        - {method.rank: 16, method.alpha: 32}
      method.dropout: {uniform: [0.0, 0.1]}              # random only
      train.learning_rate: {loguniform: [5.0e-5, 5.0e-4]}  # random only

Each trial is an ordinary run (`python -m aap_train.train --config <base> --set ...`) in its own process,
so the GPU is freed between trials, under runs/sweeps/<sweep>-<time>/. That folder holds sweep.yaml (the
sweep as run), trials.jsonl (one line per finished or failed trial: overrides, dev score, best step, last
train loss, peak GPU memory, minutes, run folder) and leaderboard.md. A resumed sweep skips the trials
already in trials.jsonl; a failed trial (out of memory, ...) is recorded and the sweep goes on.

The objective is the run's best dev score (best.json): -(answer-token loss on dev) with eval.mode loss.
It only ranks trials. Which adapter is used is decided by serving the top trials and scoring them with the
application (aap.decide.evaluate on dev), and the test split is scored once, at the end, never per trial.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import random
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .config import apply_overrides, load_config


# ------------------------------------------------------------------ trials
def _flat(dimension: str, value: Any) -> dict[str, Any]:
    """The overrides one value of a dimension sets: a dict of dotted keys, or {dimension: value}."""
    return dict(value) if isinstance(value, dict) and not _is_distribution(value) else {dimension: value}


def _is_distribution(value: Any) -> bool:
    return isinstance(value, dict) and len(value) == 1 and next(iter(value)) in ("uniform", "loguniform", "int")


def _sample(value: Any, rnd: random.Random) -> Any:
    if isinstance(value, list):
        return rnd.choice(value)
    if _is_distribution(value):
        kind, (lo, hi) = next(iter(value.items()))
        if kind == "int":
            return rnd.randint(int(lo), int(hi))
        x = math.exp(rnd.uniform(math.log(lo), math.log(hi))) if kind == "loguniform" else rnd.uniform(lo, hi)
        return float(f"{x:.3g}")
    return value


def trials(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The trials of a sweep, each {"id": "t01", "overrides": {...}} (fixed overrides included)."""
    space: dict[str, Any] = spec.get("space") or {}
    fixed = dict(spec.get("fixed") or {})
    strategy = spec.get("strategy", "grid")
    combos: list[dict[str, Any]] = []
    if strategy == "grid":
        dims = list(space.items())
        for name, values in dims:
            if not isinstance(values, list):
                raise ValueError(f"grid dimension {name!r} must be a list of values (distributions are random only)")
        for picked in itertools.product(*(values for _, values in dims)) if dims else [()]:
            o: dict[str, Any] = {}
            for (name, _), value in zip(dims, picked, strict=True):
                o.update(_flat(name, value))
            combos.append(o)
    elif strategy == "random":
        rnd = random.Random(spec.get("seed", 7))
        seen: set[str] = set()
        n = int(spec.get("trials", 8))
        for _ in range(n * 20):
            if len(combos) == n:
                break
            o = {}
            for name, values in space.items():
                o.update(_flat(name, _sample(values, rnd)))
            key = json.dumps(o, sort_keys=True)
            if key not in seen:
                seen.add(key)
                combos.append(o)
    else:
        raise ValueError(f"strategy {strategy!r}: grid or random")
    return [{"id": f"t{i:02d}", "overrides": {**fixed, **o}} for i, o in enumerate(combos, start=1)]


def _key(overrides: dict[str, Any]) -> str:
    return json.dumps(overrides, sort_keys=True, default=str)


# ------------------------------------------------------------------ results
def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def summarise_run(run: Path | None) -> dict[str, Any]:
    """Score and costs of a finished run folder (best.json, metrics.jsonl)."""
    if run is None or not run.exists():
        return {}
    best = json.loads((run / "best.json").read_text(encoding="utf-8")) if (run / "best.json").exists() else {}
    metrics = _read_jsonl(run / "metrics.jsonl")
    losses = [m["loss"] for m in metrics if m.get("event") == "log" and isinstance(m.get("loss"), int | float)]
    peaks = [m["peak_gpu_gb"] for m in metrics if isinstance(m.get("peak_gpu_gb"), int | float)]
    devs = [m for m in metrics if m.get("event") == "dev"]
    return {"score": best.get("score"), "best_step": best.get("step"), "train_loss_last": losses[-1] if losses else None,
            "dev_evals": len(devs), "peak_gpu_gb": max(peaks) if peaks else None}


def leaderboard(rows: list[dict[str, Any]]) -> str:
    done = sorted((r for r in rows if r.get("score") is not None), key=lambda r: r["score"], reverse=True)
    other = [r for r in rows if r.get("score") is None]
    out = ["| Rank | Trial | Dev loss | Best step | Last train loss | Peak GPU GB | Minutes | Overrides |",
           "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(done, start=1):
        out.append(f"| {i} | {r['id']} | {-r['score']:.4f} | {r.get('best_step')} | "
                   f"{_fmt(r.get('train_loss_last'))} | {_fmt(r.get('peak_gpu_gb'))} | {_fmt(r.get('minutes'))} | "
                   f"`{_short(r['overrides'])}` |")
    for r in other:
        out.append(f"| - | {r['id']} | {r.get('status')} | | | | {_fmt(r.get('minutes'))} | `{_short(r['overrides'])}` |")
    return "\n".join(out) + "\n"


def _fmt(v: Any) -> str:
    return "" if v is None else f"{v:.3g}" if isinstance(v, float) else str(v)


def _short(overrides: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in overrides.items())


# ------------------------------------------------------------------ running
def _train_command(base: Path, name: str, runs_dir: Path, overrides: dict[str, Any], skip_envcheck: bool) -> list[str]:
    cmd = [sys.executable, "-m", "aap_train.train", "--config", str(base), "--set", f"name={name}",
           "--set", f"logging.runs_dir={runs_dir.as_posix()}"]
    for k, v in overrides.items():
        cmd += ["--set", f"{k}={json.dumps(v)}"]
    return cmd + (["--skip-envcheck"] if skip_envcheck else [])


def _newest_run(runs_dir: Path, name: str) -> Path | None:
    found = sorted(runs_dir.glob(f"{name}-*"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def run_sweep(sweep_file: Path, *, resume: Path | None = None, dry_run: bool = False, max_trials: int | None = None,
              runner: Any = None) -> Path:
    spec = yaml.safe_load(sweep_file.read_text(encoding="utf-8"))
    name = spec["name"]
    base = (sweep_file.parent / spec["base"]).resolve() if not Path(spec["base"]).is_absolute() else Path(spec["base"])
    todo = trials(spec)
    for t in todo:                                  # every trial must be a valid run config before the first starts
        load_config(base, {**t["overrides"], "name": f"{name}-{t['id']}"})
    if dry_run:
        print(f"[sweep] {name}: {len(todo)} trials from {base.name} ({spec.get('strategy', 'grid')})")
        for t in todo:
            print(f"  {t['id']}: {_short(t['overrides'])}")
        return sweep_file
    folder = resume or Path("runs/sweeps") / f"{name}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    folder.mkdir(parents=True, exist_ok=True)
    if not (folder / "sweep.yaml").exists():
        (folder / "sweep.yaml").write_text(yaml.safe_dump({**spec, "base_resolved": str(base)}, sort_keys=False),
                                           encoding="utf-8")
    log = folder / "trials.jsonl"
    done = {_key(r["overrides"]) for r in _read_jsonl(log)}
    pending = [t for t in todo if _key(t["overrides"]) not in done][:max_trials]
    print(f"[sweep] {name}: {len(todo)} trials, {len(done)} done, running {len(pending)} in {folder}")
    run = runner or (lambda cmd: subprocess.run(cmd, check=False).returncode)
    for i, t in enumerate(pending):
        trial_name = f"{name}-{t['id']}"
        cmd = _train_command(base, trial_name, folder, t["overrides"], skip_envcheck=i > 0)
        print(f"[sweep] {t['id']}: {_short(t['overrides'])}", flush=True)
        t0 = time.monotonic()
        code = run(cmd)
        run_dir = _newest_run(folder, trial_name)
        row = {"id": t["id"], "overrides": t["overrides"], "status": "ok" if code == 0 else f"failed ({code})",
               "minutes": round((time.monotonic() - t0) / 60, 1), "run": str(run_dir) if run_dir else None,
               **summarise_run(run_dir if code == 0 else None)}
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        (folder / "leaderboard.md").write_text(leaderboard(_read_jsonl(log)), encoding="utf-8")
    board = leaderboard(_read_jsonl(log))
    (folder / "leaderboard.md").write_text(board, encoding="utf-8")
    print(board)
    return folder


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap_train.sweep", description=__doc__.split("\n")[0])
    ap.add_argument("--sweep", type=Path, help="a sweep file (configs/sweeps/*.yaml)")
    ap.add_argument("--resume", type=Path, default=None, help="a sweep folder: run the trials not done yet")
    ap.add_argument("--dry-run", action="store_true", help="list and validate the trials; no training")
    ap.add_argument("--max-trials", type=int, default=None, help="stop after this many trials (resume later)")
    ap.add_argument("--report", type=Path, default=None, help="a sweep folder: print its leaderboard")
    a = ap.parse_args(argv)
    if a.report:
        print(leaderboard(_read_jsonl(a.report / "trials.jsonl")))
        return 0
    sweep_file = a.sweep or (a.resume / "sweep.yaml" if a.resume else None)
    if sweep_file is None:
        ap.error("--sweep or --resume is required")
    if a.resume and not a.sweep:
        spec = yaml.safe_load(sweep_file.read_text(encoding="utf-8"))
        spec["base"] = spec.pop("base_resolved", spec["base"])
        sweep_file = a.resume / "sweep.resolved.yaml"
        sweep_file.write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    run_sweep(sweep_file, resume=a.resume, dry_run=a.dry_run, max_trials=a.max_trials)
    return 0


__all__ = ["apply_overrides", "leaderboard", "run_sweep", "summarise_run", "trials"]

if __name__ == "__main__":
    sys.exit(main())
