"""Train adapter A (extract) or adapter B (decide) from a run config.

    python -m aap_train.train --config configs/smoke-a-qlora.yaml [--set train.max_steps=10] [--resume <run>]
                              [--skip-envcheck] [--dry-run]

1. checks the machine (aap_train.envcheck), the config and the dataset (contract, hashes, examples);
2. creates the run folder (aap_train.tracking) with the resolved config, the dataset fingerprint and
   the git commit;
3. leaves out the examples longer than data.max_seq_len (counted in run.json), never truncating;
4. loads the base for the method (aap_train.methods) and trains with the Hugging Face Trainer;
5. at every checkpoint, generates on the dev split and keeps the best checkpoint by that score
   (best.json); the final adapter is saved under final/.

`--dry-run` stops after step 3 (no model weights loaded beyond the processor).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from .config import RunCfg, load_config


def _parse_sets(items: list[str]) -> dict[str, Any]:
    import yaml

    out = {}
    for item in items:
        key, _, value = item.partition("=")
        out[key.strip()] = yaml.safe_load(value)
    return out


TRAINER_STATE_FILES = ("optimizer.pt", "scheduler.pt", "rng_state.pth", "trainer_state.json", "training_args.bin")


def keep_best(checkpoint: Path, best: Path) -> None:
    """The weights of a checkpoint copied to best/ (save_total_limit may delete the checkpoint later);
    the optimiser and trainer state are not needed to serve it."""
    import shutil

    if best.exists():
        shutil.rmtree(best)
    shutil.copytree(checkpoint, best, ignore=shutil.ignore_patterns(*TRAINER_STATE_FILES))


def _callbacks(run: Any, cfg: RunCfg, processor: Any, dev: list[Any]) -> list[Any]:
    from transformers import TrainerCallback

    class Log(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kw):  # noqa: ANN001
            import torch

            peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else None
            run.log(**{**(logs or {}), "event": "log", "step": state.global_step, "epoch": state.epoch,
                       "peak_gpu_gb": round(peak, 2) if peak else None})

    class DevEval(TrainerCallback):
        def on_save(self, args, state, control, model=None, **kw):  # noqa: ANN001
            if not (cfg.eval.enabled and dev and model is not None):
                return
            from .evaluate import evaluate

            summary = evaluate(model, processor, dev, max_new_tokens=cfg.eval.max_new_tokens, limit=cfg.eval.limit)
            ckpt = f"checkpoints/checkpoint-{state.global_step}"
            run.log(event="dev", step=state.global_step, checkpoint=ckpt, **summary)
            best = run.read_json("best.json")
            if best is None or summary["score"] > (best.get("score") or -1):
                keep_best(run.path / ckpt, run.path / "best")
                run.write_json("best.json", {"checkpoint": "best", "from": ckpt, "step": state.global_step, **summary})

    return [Log(), DevEval()]


def train(cfg: RunCfg, *, resume: Path | None = None, skip_envcheck: bool = False, dry_run: bool = False) -> Path:
    from .collate import ChatDataset, Collator, load_processor, prepare
    from .data import load_dataset
    from .tracking import RunDir, wandb_settings

    env: dict[str, Any] = {}
    if not skip_envcheck and not dry_run:
        from .envcheck import run_checks

        env = run_checks(min_free_gb=8 if cfg.model.size_b <= 4 else 18, runs_dir=cfg.logging.runs_dir)
        if not env.get("ok"):
            failed = [k for k, c in env["checks"].items() if c["required"] and not c["ok"]]
            raise SystemExit(f"envcheck failed: {failed} (python -m aap_train.envcheck; --skip-envcheck to override)")
    ds = load_dataset(cfg.data.dataset)
    if ds.task != cfg.task:
        raise SystemExit(f"config task {cfg.task!r}, dataset task {ds.task!r}")
    max_pixels = cfg.data.max_pixels or ds.max_pixels
    processor = load_processor(cfg.model.base, revision=cfg.model.revision, max_pixels=max_pixels,
                               min_pixels=cfg.data.min_pixels, trust_remote_code=cfg.model.trust_remote_code)
    train_ex = ds.splits["train"][: cfg.data.limit_train]
    dev_ex = ds.splits["dev"][: cfg.data.limit_dev]
    prepared = prepare(train_ex, processor, cfg.data.max_seq_len)
    if not prepared.examples:
        raise SystemExit("no training example fits data.max_seq_len")
    run = RunDir(resume) if resume else RunDir.create(cfg)
    lengths = sorted(prepared.lengths)
    info = {"dataset": {"path": str(ds.root), "fingerprint": ds.fingerprint, "source": ds.meta.get("source"),
                        "counts": ds.meta.get("counts"), "prompt_version": ds.meta.get("prompt_version"),
                        "context_pack_version": ds.meta.get("context_pack_version")},
            "max_pixels": max_pixels, "train_examples": len(prepared.examples), "dev_examples": len(dev_ex),
            "too_long": prepared.too_long,
            "tokens": {"min": lengths[0], "median": lengths[len(lengths) // 2], "max": lengths[-1]},
            "env": {k: env.get(k) for k in ("torch", "cuda_build", "gpu", "transformers", "peft")}}
    print(f"[train] {cfg.name}: {len(prepared.examples)} train / {len(dev_ex)} dev examples, "
          f"{len(prepared.too_long)} too long; tokens {info['tokens']}; run folder {run.path}")
    if dry_run:
        run.describe(**info, dry_run=True)
        return run.path

    import torch
    from transformers import Trainer, TrainingArguments

    from .methods import apply_method, load_model, trainable_summary

    report_to = wandb_settings(cfg)
    torch.manual_seed(cfg.train.seed)
    model = apply_method(load_model(cfg), cfg)
    info["trainable"] = trainable_summary(model)
    run.describe(**info)
    print(f"[train] trainable parameters: {info['trainable']}")
    t = cfg.train
    args = TrainingArguments(
        output_dir=str(run.checkpoints), run_name=run.path.name, report_to=report_to,
        per_device_train_batch_size=t.batch_size, gradient_accumulation_steps=t.grad_accum,
        num_train_epochs=t.epochs, max_steps=t.max_steps, learning_rate=t.learning_rate,
        lr_scheduler_type=t.scheduler, warmup_steps=t.warmup_ratio,   # a float < 1 is a ratio (transformers 5)
        weight_decay=t.weight_decay,
        max_grad_norm=t.max_grad_norm, optim=cfg.optimizer, bf16=True, logging_steps=t.logging_steps,
        save_strategy="steps", save_steps=t.save_steps, save_total_limit=t.save_total_limit, seed=t.seed,
        gradient_checkpointing=False,           # set up by aap_train.methods (non-reentrant)
        remove_unused_columns=False, dataloader_num_workers=0, dataloader_pin_memory=False,
    )
    tok = getattr(processor, "tokenizer", processor)
    trainer = Trainer(model=model, args=args, train_dataset=ChatDataset(prepared.examples, processor, cfg.data.max_seq_len),
                      data_collator=Collator(tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id),
                      callbacks=_callbacks(run, cfg, processor, dev_ex))
    last = None
    if resume:
        ckpts = sorted(run.checkpoints.glob("checkpoint-*"), key=lambda p: int(p.name.split("-")[-1]))
        last = str(ckpts[-1]) if ckpts else None
    result = trainer.train(resume_from_checkpoint=last)
    run.log(event="train_end", **{k: v for k, v in result.metrics.items() if isinstance(v, int | float)})
    trainer.save_model(str(run.path / "final"))
    if cfg.method.name == "full":
        processor.save_pretrained(str(run.path / "final"))
    if run.read_json("best.json") is None:
        run.write_json("best.json", {"checkpoint": "final", "step": trainer.state.global_step, "score": None,
                                     "note": "no dev evaluation ran"})
    print(f"[train] done: {run.path} (best: {run.read_json('best.json')})")
    return run.path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap_train.train", description=__doc__.split("\n")[0])
    ap.add_argument("--config", type=Path, required=True)
    ap.add_argument("--set", dest="sets", action="append", default=[], help="override, e.g. train.max_steps=10")
    ap.add_argument("--resume", type=Path, default=None, help="a run folder: continue from its last checkpoint")
    ap.add_argument("--skip-envcheck", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="check config and data, count tokens; no training")
    a = ap.parse_args(argv)
    cfg = load_config(a.resume / "config.yaml" if a.resume else a.config, _parse_sets(a.sets))
    train(cfg, resume=a.resume, skip_envcheck=a.skip_envcheck, dry_run=a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
