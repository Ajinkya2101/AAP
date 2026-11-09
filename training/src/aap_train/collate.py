"""Contract examples -> model inputs.

- The messages become the model's chat template; the target is the assistant turn.
- Loss is on the answer only: every token of the prompt (system, user, page images) is masked.
- Images are scaled by the processor with the dataset's `max_pixels`, the value vLLM serves with, so a
  page has the same number of image tokens in training as in production.
- An example longer than `max_seq_len` is left out (and counted), never truncated: a cut answer
  would teach the model to stop in the middle of the JSON.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .data import Example

IGNORE = -100


# ------------------------------------------------------------------ pure parts
def chat_messages(ex: Example, *, with_answer: bool) -> tuple[list[dict[str, Any]], list[str]]:
    """The example as chat-template messages (image parts as placeholders) and its image paths."""
    images: list[str] = []
    out = []
    for m in ex.messages:
        parts = []
        for p in m["content"]:
            if p["type"] == "image":
                images.append(str(ex.root / p["path"]))
                parts.append({"type": "image"})
            else:
                parts.append({"type": "text", "text": p["text"]})
        out.append({"role": m["role"], "content": parts})
    if with_answer:
        out.append({"role": "assistant", "content": [{"type": "text", "text": ex.target}]})
    return out, images


def mask_prompt(input_ids: list[int], prompt_len: int) -> list[int]:
    """Labels: the prompt masked, the answer kept."""
    if not 0 < prompt_len < len(input_ids):
        raise ValueError(f"prompt length {prompt_len} for {len(input_ids)} tokens: no answer tokens left")
    return [IGNORE] * prompt_len + list(input_ids[prompt_len:])


def pad(rows: list[list[int]], value: int) -> list[list[int]]:
    width = max(len(r) for r in rows)
    return [r + [value] * (width - len(r)) for r in rows]


# -------------------------------------------------------------- with a processor
def load_processor(base: str, *, revision: str | None = None, max_pixels: int | None = None,
                   min_pixels: int | None = None, trust_remote_code: bool = False) -> Any:
    import warnings

    from transformers import AutoProcessor

    # the processor repeats this deprecation on every call (see below why the keywords are kept)
    warnings.filterwarnings("ignore", category=FutureWarning, message=r"Passing .min_pixels. and .max_pixels.")
    kw: dict[str, Any] = {"revision": revision, "trust_remote_code": trust_remote_code}
    ip_kw = {"max_pixels": max_pixels, "min_pixels": min_pixels or 3136} if max_pixels else {}
    # max_pixels / min_pixels, as vLLM passes them (--mm-processor-kwargs). transformers 5 deprecates them
    # for size=..., but a size= given to from_pretrained is overridden by the saved processor config
    # (measured: 4,819 instead of 1,240 image tokens for one page); check_image_limit catches a change
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        try:
            processor = AutoProcessor.from_pretrained(base, **kw, **ip_kw)
        except (TypeError, ValueError):         # a processor without image settings (text-only base)
            processor = AutoProcessor.from_pretrained(base, **kw)
    if max_pixels and getattr(processor, "image_processor", None) is not None:
        check_image_limit(processor, max_pixels)
    return processor


def check_image_limit(processor: Any, max_pixels: int) -> int:
    """A page far larger than the limit must come out at most `max_pixels`, as vLLM serves it; returns
    the image tokens of that page. Raises when the processor ignores the limit."""
    import warnings

    from PIL import Image

    ip = processor.image_processor
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        grid = ip(images=[Image.new("RGB", (3400, 4400), "white")], return_tensors="pt")["image_grid_thw"][0]
    patch = int(getattr(ip, "patch_size", 14))
    pixels = int(grid[1]) * int(grid[2]) * patch * patch
    if pixels > max_pixels:
        raise RuntimeError(f"the image processor ignores max_pixels={max_pixels} ({pixels} pixels after resizing): "
                           "training would see larger pages than vLLM serves")
    merge = int(getattr(ip, "merge_size", 2))
    return int(grid.prod()) // (merge * merge)


def _tokenizer(processor: Any) -> Any:
    return getattr(processor, "tokenizer", processor)


def _encode_text(processor: Any, text: str, images: list[Any]) -> dict[str, Any]:
    if images:
        return processor(text=[text], images=images, return_tensors="pt")
    return processor(text=[text], return_tensors="pt") if hasattr(processor, "tokenizer") \
        else processor([text], return_tensors="pt")


def encode(ex: Example, processor: Any, max_seq_len: int) -> dict[str, Any] | None:
    """input_ids, attention_mask, labels (and pixel_values, image_grid_thw); None when too long."""
    from PIL import Image

    prompt_msgs, paths = chat_messages(ex, with_answer=False)
    full_msgs, _ = chat_messages(ex, with_answer=True)
    template = processor if hasattr(processor, "apply_chat_template") else _tokenizer(processor)
    prompt_text = template.apply_chat_template(prompt_msgs, tokenize=False, add_generation_prompt=True)
    full_text = template.apply_chat_template(full_msgs, tokenize=False, add_generation_prompt=False)
    if not full_text.startswith(prompt_text):
        raise ValueError("the chat template does not put the prompt before the answer; cannot mask the prompt")
    images = [Image.open(p).convert("RGB") for p in paths]
    full = _encode_text(processor, full_text, images)
    ids = full["input_ids"][0].tolist()
    if len(ids) > max_seq_len:
        return None
    prompt_len = int(_encode_text(processor, prompt_text, images)["input_ids"].shape[1])
    out = {"input_ids": ids, "attention_mask": full["attention_mask"][0].tolist(), "labels": mask_prompt(ids, prompt_len)}
    for key in ("pixel_values", "image_grid_thw"):
        if key in full:
            out[key] = full[key]
    return out


@dataclass
class Prepared:
    """The examples that fit, with their lengths; the rest counted."""
    examples: list[Example]
    lengths: list[int]
    too_long: list[str] = field(default_factory=list)


def prepare(examples: list[Example], processor: Any, max_seq_len: int) -> Prepared:
    p = Prepared(examples=[], lengths=[])
    for ex in examples:
        enc = encode(ex, processor, max_seq_len)
        if enc is None:
            p.too_long.append(ex.id)
            continue
        p.examples.append(ex)
        p.lengths.append(len(enc["input_ids"]))
    return p


class ChatDataset:
    """torch-style dataset: encodes on access (page images are not kept in memory)."""

    def __init__(self, examples: list[Example], processor: Any, max_seq_len: int):
        self.examples, self.processor, self.max_seq_len = examples, processor, max_seq_len

    def __len__(self) -> int:
        return len(self.examples)

    def __getitem__(self, i: int) -> dict[str, Any]:
        enc = encode(self.examples[i], self.processor, self.max_seq_len)
        if enc is None:                 # prepare() leaves these out; a changed processor would surface here
            raise ValueError(f"example {self.examples[i].id} is longer than {self.max_seq_len} tokens")
        return enc


class Collator:
    def __init__(self, pad_token_id: int):
        self.pad_token_id = pad_token_id

    def __call__(self, batch: list[dict[str, Any]]) -> dict[str, Any]:
        import torch

        out: dict[str, Any] = {
            "input_ids": torch.tensor(pad([b["input_ids"] for b in batch], self.pad_token_id)),
            "attention_mask": torch.tensor(pad([b["attention_mask"] for b in batch], 0)),
            "labels": torch.tensor(pad([b["labels"] for b in batch], IGNORE)),
        }
        for key in ("pixel_values", "image_grid_thw"):
            parts = [b[key] for b in batch if key in b]
            if parts:
                out[key] = torch.cat(parts, dim=0)
        return out
