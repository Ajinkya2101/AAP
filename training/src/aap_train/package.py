"""Package a run's chosen checkpoint for serving.

    python -m aap_train.package --run runs/<run> [--checkpoint checkpoints/checkpoint-100] [--max-lora-rank 16]

- qlora / lora: `adapter/` for vLLM (`--enable-lora --lora-modules <name>=<path>`): the adapter config and
  weights, checked first (LoRA weights only, language-model modules only, rank within vLLM's
  --max-lora-rank), plus aap-adapter.json (base model and revision, method, dataset fingerprint).
- lora_projector / full: `model/`, a merged bf16 model with its processor, served as its own vLLM model,
  because a vLLM LoRA adapter cannot carry fully trained (non-LoRA) weights.

`--slot read|decide` also installs the adapter into training/adapters/<slot>/, the folders
docker-compose.lora.yml serves (read = adapter A, decide = adapter B).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from .methods import VISION_MARKERS


def check_adapter(config: dict[str, Any], weight_names: list[str], *, max_rank: int = 16) -> list[str]:
    """What stops vLLM from serving this adapter (empty when it can)."""
    problems = []
    if config.get("modules_to_save"):
        problems.append(f"modules_to_save {config['modules_to_save']}: fully trained weights; serve a merged model")
    if int(config.get("r") or 0) > max_rank:
        problems.append(f"rank {config.get('r')} > --max-lora-rank {max_rank}")
    if config.get("use_dora"):
        problems.append("DoRA adapters are not served by vLLM LoRA")
    not_lora = [n for n in weight_names if "lora_" not in n]
    if not_lora:
        problems.append(f"{len(not_lora)} non-LoRA weight(s), e.g. {not_lora[0]}")
    vision = [n for n in weight_names if any(m in n for m in VISION_MARKERS)]
    if vision:
        problems.append(f"{len(vision)} weight(s) on the vision tower, e.g. {vision[0]}")
    return problems


def _weights(path: Path) -> list[str]:
    from safetensors import safe_open

    with safe_open(str(path), framework="pt") as f:
        return list(f.keys())


def package_adapter(run: Path, checkpoint: Path, *, max_rank: int = 16, meta: dict[str, Any]) -> Path:
    config = json.loads((checkpoint / "adapter_config.json").read_text(encoding="utf-8"))
    weights = checkpoint / "adapter_model.safetensors"
    problems = check_adapter(config, _weights(weights), max_rank=max_rank)
    if problems:
        raise SystemExit("adapter cannot be served by vLLM LoRA:\n  " + "\n  ".join(problems))
    out = run / "adapter"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    for name in ("adapter_config.json", "adapter_model.safetensors"):
        shutil.copy2(checkpoint / name, out / name)
    (out / "aap-adapter.json").write_text(json.dumps({**meta, "checkpoint": str(checkpoint)}, indent=2) + "\n",
                                          encoding="utf-8")
    return out


SLOTS = {"read": "extract", "decide": "decide"}
ADAPTERS_DIR = Path(__file__).resolve().parents[2] / "adapters"


def install(adapter: Path, slot: str, task: str, root: Path = ADAPTERS_DIR) -> Path:
    """The packaged adapter copied into a serving slot (replacing what was there)."""
    if SLOTS.get(slot) != task:
        raise SystemExit(f"slot {slot!r} serves the {SLOTS.get(slot)!r} adapter, this run is {task!r}")
    target = root / slot
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(adapter, target)
    return target


def package_merged(run: Path, checkpoint: Path, cfg: Any) -> Path:
    import torch
    import transformers

    from .methods import model_class

    out = run / "model"
    cls = model_class(cfg)
    model = cls.from_pretrained(checkpoint if cfg.method.name == "full" else cfg.model.base,
                                revision=None if cfg.method.name == "full" else cfg.model.revision,
                                dtype=torch.bfloat16, low_cpu_mem_usage=True)
    if cfg.method.name != "full":
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, str(checkpoint)).merge_and_unload()
    model.save_pretrained(out, safe_serialization=True)
    transformers.AutoProcessor.from_pretrained(cfg.model.base, revision=cfg.model.revision).save_pretrained(out)
    return out


def main(argv: list[str] | None = None) -> int:
    from .config import load_config
    from .tracking import RunDir

    ap = argparse.ArgumentParser(prog="python -m aap_train.package", description=__doc__.split("\n")[0])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, default=None, help="default: best.json, else the final checkpoint")
    ap.add_argument("--max-lora-rank", type=int, default=16)
    ap.add_argument("--slot", choices=sorted(SLOTS), default=None,
                    help="also install the adapter for docker-compose.lora.yml (read = A, decide = B)")
    a = ap.parse_args(argv)
    run = RunDir(a.run)
    cfg = load_config(a.run / "config.yaml")
    best = run.read_json("best.json")
    ckpt = a.checkpoint or (Path(best["checkpoint"]) if best else Path("final"))
    ckpt = ckpt if ckpt.is_absolute() else a.run / ckpt
    info = run.read_json("run.json") or {}
    meta = {"base": cfg.model.base, "revision": cfg.model.revision, "method": cfg.method.name, "task": cfg.task,
            "dataset": info.get("dataset"), "git_commit": info.get("git_commit")}
    out = package_merged(a.run, ckpt, cfg) if cfg.merged_serving else \
        package_adapter(a.run, ckpt, max_rank=a.max_lora_rank, meta=meta)
    print(f"[package] {cfg.method.name}: {out}")
    if a.slot:
        if cfg.merged_serving:
            raise SystemExit("a merged model is served as its own vLLM model, not in an adapter slot")
        print(f"[package] installed in slot {a.slot}: {install(out, a.slot, cfg.task)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
