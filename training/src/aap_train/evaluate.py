"""Generate on the dev split and score the answers (aap_train.metrics), to choose a checkpoint.

    python -m aap_train.evaluate --run runs/<run> [--checkpoint checkpoints/checkpoint-100] [--limit 20]

Greedy decoding, the prompt exactly as in training. Answers are not written anywhere: only the scores
go to metrics.jsonl (client data stays out of the logs). The decision to use an adapter is made with
the application's evaluation on the served model (pipeline 1), not with this score.
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
    ap.add_argument("--checkpoint", type=Path, default=None, help="relative to the run; default: best.json, else base")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--split", default="dev")
    a = ap.parse_args(argv)
    run = RunDir(a.run)
    cfg = load_config(a.run / "config.yaml")
    ds = load_dataset(cfg.data.dataset, splits=(a.split,))
    processor = load_processor(cfg.model.base, revision=cfg.model.revision,
                               max_pixels=cfg.data.max_pixels or ds.max_pixels, min_pixels=cfg.data.min_pixels)
    model = load_model(cfg)
    ckpt = a.checkpoint or (Path(b["checkpoint"]) if (b := run.read_json("best.json")) else None)
    if ckpt is not None and cfg.method.name != "full":
        from peft import PeftModel

        model = PeftModel.from_pretrained(model, str(a.run / ckpt if not ckpt.is_absolute() else ckpt))
    summary = evaluate(model, processor, ds.splits[a.split], max_new_tokens=cfg.eval.max_new_tokens,
                       limit=a.limit or cfg.eval.limit)
    run.log(event="evaluate", split=a.split, checkpoint=str(ckpt) if ckpt else "base", **summary)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
