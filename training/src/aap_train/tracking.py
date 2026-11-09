"""The run folder and the optional Weights & Biases logging.

    runs/<name>-<UTC time>/
        config.yaml        the resolved run config
        run.json           dataset fingerprint and counts, git commit, environment, trainable parameters
        metrics.jsonl      one line per log step and per dev evaluation (loss, lr, peak GPU memory, scores)
        checkpoints/       trainer checkpoints
        best.json          the checkpoint with the best dev score
        adapter/ | model/  the packaged result (aap_train.package)

W&B rules (docs/PLAN-TRAINING-PIPELINE.md §8): metrics and the config only. No tables, predictions,
images, datasets, code or model artifacts; offline unless the config says online.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from .config import RunCfg

WANDB_ENV = {"WANDB_LOG_MODEL": "false", "WANDB_DISABLE_CODE": "true", "WANDB_DISABLE_GIT": "true",
             "WANDB_WATCH": "false", "WANDB_SILENT": "true"}


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _git_dirty() -> bool | None:
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", "."], capture_output=True, text=True, check=True,
                             timeout=10).stdout
        return bool(out.strip())
    except (OSError, subprocess.SubprocessError):
        return None


class RunDir:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def create(cls, cfg: RunCfg, now: datetime | None = None) -> RunDir:
        stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
        path = Path(cfg.logging.runs_dir) / f"{cfg.name}-{stamp}"
        path.mkdir(parents=True, exist_ok=False)
        (path / "config.yaml").write_text(yaml.safe_dump(json.loads(cfg.model_dump_json()), sort_keys=False),
                                          encoding="utf-8")
        return cls(path)

    @property
    def checkpoints(self) -> Path:
        return self.path / "checkpoints"

    def write_json(self, name: str, data: dict[str, Any]) -> None:
        (self.path / name).write_text(json.dumps(data, indent=2, default=str) + "\n", encoding="utf-8")

    def read_json(self, name: str) -> dict[str, Any] | None:
        p = self.path / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    def log(self, **values: Any) -> None:
        values.setdefault("time", datetime.now(UTC).isoformat(timespec="seconds"))
        with (self.path / "metrics.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(values, default=str) + "\n")

    def describe(self, **extra: Any) -> None:
        self.write_json("run.json", {"git_commit": _git_commit(), "git_dirty": _git_dirty(), **extra})


def wandb_settings(cfg: RunCfg) -> list[str]:
    """The Trainer's report_to; sets the W&B environment to metrics-only before the Trainer starts."""
    if not cfg.logging.wandb:
        os.environ["WANDB_DISABLED"] = "true"
        return []
    try:
        import wandb  # noqa: F401
    except ImportError as exc:
        raise SystemExit("logging.wandb is on but wandb is not installed: uv sync --extra wandb") from exc
    for k, v in WANDB_ENV.items():
        os.environ[k] = v
    os.environ["WANDB_MODE"] = cfg.logging.wandb_mode
    os.environ["WANDB_PROJECT"] = cfg.logging.wandb_project
    os.environ.pop("WANDB_DISABLED", None)
    return ["wandb"]
