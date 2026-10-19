"""python -m aap.eval --dir <invoicegen set> [--name baseline] [--only S1,S3] [--limit 20] [--json out.json]
   python -m aap.eval --rescore <eval run id>      # new metrics over stored results, no model calls
   python -m aap.eval --dir <set> --resume <run id>  # continue an interrupted run
   python -m aap.eval --dir <set> --agents           # also run the agents (for aap.eval.model_use)

Runs the set through stages 1-8 with the configured LLM_BACKEND and prints the report
(per tier and field, plus the PLAN 4 decision gates). Exit code 0 = all gates pass.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

from .harness import rescore, run_eval
from .report import render


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m aap.eval")
    p.add_argument("--dir", type=Path)
    p.add_argument("--rescore", type=uuid.UUID, default=None, help="score a finished run again (no model calls)")
    p.add_argument("--resume", type=uuid.UUID, default=None, help="continue an interrupted run (with --dir)")
    p.add_argument("--name", default="eval")
    p.add_argument("--only", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--agents", action="store_true", help="after scoring, run the agents on each invoice")
    p.add_argument("--json", type=Path, default=None, help="also write the metrics here")
    a = p.parse_args(argv)
    if a.rescore:
        run_id, metrics = rescore(a.rescore)
    elif a.dir:
        run_id, metrics = run_eval(a.dir, name=a.name, only=set(a.only.split(",")) if a.only else None,
                                   limit=a.limit, resume=a.resume, agents=a.agents)
    else:
        p.error("--dir or --rescore is required")
    print(render(run_id, a.name, metrics))
    if a.json:
        a.json.write_text(json.dumps({"run_id": str(run_id), **metrics}, indent=2), encoding="utf-8")
    return 0 if all(g["pass"] for g in metrics["gates"].values()) else 1


if __name__ == "__main__":
    sys.exit(main())
