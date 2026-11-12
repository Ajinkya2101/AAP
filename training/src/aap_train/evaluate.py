"""Score a checkpoint on the dev split, to choose a checkpoint.

    python -m aap_train.evaluate --run runs/<run> [--checkpoint best] [--limit 20] [--mode loss|generate]

- loss (default): the mean loss on the answer tokens, teacher forced; the score is -loss.
- generate: greedy generation, the prompt exactly as in training, scored field by field
  (aap_train.metrics). Free generation is not the served decoding (vLLM enforces the JSON schema), so
  invented keys make answers invalid here that would be valid in production.

Answers are not written anywhere: only the scores go to metrics.jsonl (client data stays out of the
logs). The decision to use an adapter is made with the application's evaluation on the served model
(pipeline 1), not with this score.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from .collate import chat_messages
from .data import Example
from .metrics import headline, score_one, summarise


def generate(model: Any, processor: Any, ex: Example, max_new_tokens: int) -> str:
    import torch
    from PIL import Image

    msgs, paths = chat_messages(ex, with_answer=False)
    template = processor if hasattr(processor, "apply_chat_template") else processor.tokenizer
    text = template.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    images = [Image.open(p).convert("RGB") for p in paths]
    inputs = processor(text=[text], images=images, return_tensors="pt") if images \
        else processor(text=[text], return_tensors="pt")
    inputs = {k: v.to(model.device) for k, v in inputs.items()}
    tok = getattr(processor, "tokenizer", processor)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                             pad_token_id=tok.pad_token_id or tok.eos_token_id)
    return tok.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)


def dev_loss(model: Any, processor: Any, examples: list[Example], *, max_seq_len: int = 8192,
             limit: int | None = None) -> dict[str, Any]:
    """Mean loss over the answer tokens of the dev examples (each answer token weighs the same)."""
    import torch

    from .collate import IGNORE, Collator, encode

    was_training = model.training
    model.eval()
    tok = getattr(processor, "tokenizer", processor)
    collate = Collator(tok.pad_token_id if tok.pad_token_id is not None else tok.eos_token_id)
    total, tokens, skipped = 0.0, 0, 0
    t0 = time.monotonic()
    for ex in examples[:limit]:
        enc = encode(ex, processor, max_seq_len)
        if enc is None:
            skipped += 1
            continue
        batch = {k: v.to(model.device) for k, v in collate([enc]).items()}
        with torch.no_grad():
            # the model's own (shifted, answer-only) mean loss: the same computation as a training step
            loss = model(**batch).loss
        n = int((batch["labels"][:, 1:] != IGNORE).sum())
        total, tokens = total + float(loss) * n, tokens + n
    if was_training:
        model.train()
    mean = total / tokens if tokens else float("nan")
    return {"examples": len(examples[:limit]) - skipped, "answer_tokens": tokens, "loss": round(mean, 5),
            "score": -mean, "seconds_per_example": round((time.monotonic() - t0) / max(len(examples[:limit]), 1), 2)}


def score_checkpoint(model: Any, processor: Any, examples: list[Example], cfg: Any) -> dict[str, Any]:
    if cfg.eval.mode == "loss":
        return dev_loss(model, processor, examples, max_seq_len=cfg.data.max_seq_len, limit=cfg.eval.limit)
    return evaluate(model, processor, examples, max_new_tokens=cfg.eval.max_new_tokens, limit=cfg.eval.limit)


def evaluate(model: Any, processor: Any, examples: list[Example], *, max_new_tokens: int = 4096,
             limit: int | None = None) -> dict[str, Any]:
    was_training = model.training
    model.eval()
    use_cache = getattr(model.config, "use_cache", None)
    model.config.use_cache = True
    t0 = time.monotonic()
    scores = [score_one(generate(model, processor, ex, max_new_tokens), ex.target_json)
              for ex in examples[:limit] if ex]
    if use_cache is not None:
        model.config.use_cache = use_cache
    if was_training:
        model.train()
    summary = summarise(scores)
    summary["score"] = headline(summary)
    summary["seconds_per_example"] = round((time.monotonic() - t0) / max(len(scores), 1), 2)
    return summary


def main(argv: list[str] | None = None) -> int:
    from .collate import load_processor
    from .config import load_config
    from .data import load_dataset
    from .methods import load_model
    from .tracking import RunDir

    ap = argparse.ArgumentParser(prog="python -m aap_train.evaluate", description=__doc__.split("\n")[0])
    ap.add_argument("--run", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, default=None, help="relative to the run, or base (no adapter); default: best.json, else base")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--mode", choices=["loss", "generate"], default=None, help="default: eval.mode of the run")
    a = ap.parse_args(argv)
    run = RunDir(a.run)
    cfg = load_config(a.run / "config.yaml", {"eval.mode": a.mode} if a.mode else None)
    ds = load_dataset(cfg.data.dataset, splits=(a.split,))
    processor = load_processor(cfg.model.base, revision=cfg.model.revision,
                               max_pixels=cfg.data.max_pixels or ds.max_pixels, min_pixels=cfg.data.min_pixels)
    model = load_model(cfg)
    ckpt = a.checkpoint or (Path(b["checkpoint"]) if (b := run.read_json("best.json")) else None)
    if ckpt is not None and str(ckpt) == "base":          # the untrained base, for comparison
        ckpt = None
    if ckpt is not None and cfg.method.name != "full":
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, str(a.run / ckpt if not ckpt.is_absolute() else ckpt))
    if a.limit:
        cfg.eval.limit = a.limit
    summary = score_checkpoint(model, processor, ds.splits[a.split], cfg)
    run.log(event="evaluate", split=a.split, mode=cfg.eval.mode, checkpoint=str(ckpt) if ckpt else "base", **summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
