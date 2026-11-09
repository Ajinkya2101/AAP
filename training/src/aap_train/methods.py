"""The four training methods: how the base model is loaded and which weights train.

| method          | base weights                  | trains                                     | served as      |
|-----------------|-------------------------------|--------------------------------------------|----------------|
| qlora           | 4-bit NF4 (vision tower bf16) | LoRA on the language model                 | vLLM adapter   |
| lora            | bf16                          | LoRA on the language model                 | vLLM adapter   |
| lora_projector  | 4-bit NF4 (vision tower bf16) | LoRA + the vision-language merger in full  | merged model   |
| full            | bf16 (small models only)      | the language model (and merger) in full    | full checkpoint|

The vision encoder stays frozen in every method (`method.freeze_vision`). LoRA targets only the
language model: modules under the vision tower are excluded by name, because the Qwen2.5-VL vision
blocks reuse the same projection names (gate_proj, up_proj, down_proj).
"""

from __future__ import annotations

import re
from typing import Any

from .config import RunCfg

VISION_MARKERS = ("visual", "vision_tower", "vision_model", "image_encoder")
PROJECTOR_MARKERS = ("merger", "multi_modal_projector", "mm_projector")


def lm_target_regex(targets: list[str]) -> str:
    """PEFT target_modules regex: the projection names, outside the vision tower."""
    names = "|".join(re.escape(t) for t in targets)
    vision = "|".join(VISION_MARKERS)
    return rf"^(?!.*(?:{vision})).*\.(?:{names})$"


def lora_targets(module_names: list[str], targets: list[str]) -> list[str]:
    pattern = re.compile(lm_target_regex(targets))
    return [n for n in module_names if pattern.match(n)]


def is_vision(name: str) -> bool:
    return any(m in name for m in VISION_MARKERS) and not is_projector(name)


def is_projector(name: str) -> bool:
    return any(m in name for m in PROJECTOR_MARKERS)


def model_class(cfg: RunCfg) -> Any:
    """The vision-language class for a base with a vision part (also for text-only decide examples on it),
    else the causal LM class."""
    import transformers

    conf = transformers.AutoConfig.from_pretrained(cfg.model.base, revision=cfg.model.revision,
                                                   trust_remote_code=cfg.model.trust_remote_code)
    if getattr(conf, "vision_config", None) is not None:
        return getattr(transformers, "AutoModelForImageTextToText", None) or transformers.AutoModelForVision2Seq
    return transformers.AutoModelForCausalLM


def load_model(cfg: RunCfg) -> Any:
    import torch
    import transformers

    kw: dict[str, Any] = {"revision": cfg.model.revision, "attn_implementation": cfg.model.attn,
                          "trust_remote_code": cfg.model.trust_remote_code, "low_cpu_mem_usage": True,
                          "dtype": torch.bfloat16}
    if cfg.quantized:
        kw["quantization_config"] = transformers.BitsAndBytesConfig(
            load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=torch.bfloat16,
            # the vision tower, its projector and the output head stay bf16
            llm_int8_skip_modules=skip_modules())
        kw["device_map"] = {"": 0}
    cls = model_class(cfg)
    try:
        model = cls.from_pretrained(cfg.model.base, **kw)
    except TypeError:                       # transformers < 5 names it torch_dtype
        kw["torch_dtype"] = kw.pop("dtype")
        model = cls.from_pretrained(cfg.model.base, **kw)
    if not cfg.quantized and torch.cuda.is_available():
        model = model.to("cuda")
    if cfg.quantized:
        wrong = quantized_outside_language_model(model)
        if wrong:
            raise RuntimeError(f"{len(wrong)} vision / projector module(s) were quantized to 4-bit (e.g. {wrong[0]}): "
                               "the skip list does not match this model's module names")
    return model


def skip_modules() -> list[str]:
    """Modules kept out of 4-bit quantization. transformers 5 matches each entry from the start of the
    module path (as a prefix or a regex) or as a suffix, so the vision tower needs `.*visual`; older
    versions match a plain name anywhere in the path. Both forms are listed."""
    names = [*VISION_MARKERS, *PROJECTOR_MARKERS]
    return [*names, *(rf".*{n}" for n in names), "lm_head"]


def quantized_outside_language_model(model: Any) -> list[str]:
    return [n for n, mod in model.named_modules()
            if type(mod).__name__ == "Linear4bit" and (is_vision(n) or is_projector(n))]


def apply_method(model: Any, cfg: RunCfg) -> Any:
    m = cfg.method
    if m.name == "full":
        for name, p in model.named_parameters():
            p.requires_grad = not (m.freeze_vision and is_vision(name))
        if cfg.train.gradient_checkpointing:
            model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        return model
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training

    if cfg.quantized:
        model = prepare_model_for_kbit_training(
            model, use_gradient_checkpointing=cfg.train.gradient_checkpointing,
            gradient_checkpointing_kwargs={"use_reentrant": False})
    elif cfg.train.gradient_checkpointing:
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
        model.enable_input_require_grads()
    names = [n for n, _ in model.named_modules()]
    targets = lora_targets(names, m.target_modules)
    if not targets:
        raise ValueError(f"no language-model module matches {m.target_modules}")
    projector = sorted({n.split(".")[-1] for n in names if is_projector(n) and n.split(".")[-1] in PROJECTOR_MARKERS})
    lora = LoraConfig(r=m.rank, lora_alpha=m.alpha, lora_dropout=m.dropout, bias="none", task_type="CAUSAL_LM",
                      target_modules=lm_target_regex(m.target_modules),
                      modules_to_save=projector if m.name == "lora_projector" else None)
    model = get_peft_model(model, lora)
    if m.freeze_vision:
        for name, p in model.named_parameters():
            if is_vision(name) and "lora_" not in name:
                p.requires_grad = False
    return model


def trainable_summary(model: Any) -> dict[str, Any]:
    total = trainable = 0
    for _, p in model.named_parameters():
        n = p.numel()
        total += n
        trainable += n if p.requires_grad else 0
    return {"parameters": total, "trainable": trainable, "trainable_share": round(trainable / total, 6) if total else 0}
