"""The run configuration (one YAML file per run) and the combinations it refuses.

    model:    base model, its size, attention backend
    method:   qlora | lora | lora_projector | full, LoRA settings
    data:     an aap-train-chat/1 dataset folder, sequence length
    train:    optimiser and schedule
    eval:     dev-split generation scoring used to pick the checkpoint
    logging:  run folder; optional Weights & Biases (metrics and config only)
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Method = Literal["qlora", "lora", "lora_projector", "full"]
FULL_MAX_BILLION_PARAMS = 4.0       # a full fine-tune of anything bigger does not fit one 24 GB GPU
LORA_BF16_MAX_BILLION_PARAMS = 4.0  # bf16 LoRA on a 7B base with page images does not fit either
DEFAULT_TARGETS = ["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelCfg(_Strict):
    base: str = "Qwen/Qwen2.5-VL-7B-Instruct"
    revision: str | None = None                       # pin to the revision vLLM serves
    size_b: float = 7.0                               # parameters in billions (for the memory checks)
    attn: Literal["sdpa", "eager", "flash_attention_2"] = "sdpa"
    trust_remote_code: bool = False


class MethodCfg(_Strict):
    name: Method = "qlora"
    rank: int = 16
    alpha: int = 32
    dropout: float = 0.05
    target_modules: list[str] = Field(default_factory=lambda: list(DEFAULT_TARGETS))
    freeze_vision: bool = True                        # the vision tower stays frozen (all methods)
    allow_large: bool = False                         # override the memory refusals (another GPU)


class DataCfg(_Strict):
    dataset: Path
    max_seq_len: int = 8192
    max_pixels: int | None = None                     # default: the dataset's (= what vLLM serves)
    min_pixels: int = 3136
    limit_train: int | None = None
    limit_dev: int | None = None


class TrainCfg(_Strict):
    epochs: float = 2.0
    max_steps: int = -1                               # > 0 wins over epochs (smoke runs)
    learning_rate: float = 1e-4
    scheduler: str = "cosine"
    warmup_ratio: float = 0.03
    batch_size: int = 1
    grad_accum: int = 8
    gradient_checkpointing: bool = True
    weight_decay: float = 0.0
    max_grad_norm: float = 1.0
    optim: str | None = None                          # default: paged_adamw_8bit for 4-bit bases, adamw_torch else
    save_steps: int = 50
    save_total_limit: int = 3
    logging_steps: int = 5
    seed: int = 7


class EvalCfg(_Strict):
    enabled: bool = True
    # loss: the answer-token loss on dev (teacher forced), lower is better; cheap, and what training optimises.
    # generate: free greedy generation scored field by field. Production decodes with the JSON schema
    # enforced (vLLM), free generation does not: a model that invents keys scores 0 here and fine there
    # (smoke run 2026-11-09). The served model is judged by pipeline 1 (aap.eval), not by either.
    mode: Literal["loss", "generate"] = "loss"
    limit: int | None = 50                            # dev examples per checkpoint
    max_new_tokens: int = 4096                        # generate mode


class LoggingCfg(_Strict):
    runs_dir: Path = Path("runs")
    wandb: bool = False
    wandb_project: str = "aap-train"
    wandb_mode: Literal["offline", "online"] = "offline"


class RunCfg(_Strict):
    name: str
    task: Literal["extract", "decide"]
    model: ModelCfg = Field(default_factory=ModelCfg)
    method: MethodCfg = Field(default_factory=MethodCfg)
    data: DataCfg
    train: TrainCfg = Field(default_factory=TrainCfg)
    eval: EvalCfg = Field(default_factory=EvalCfg)
    logging: LoggingCfg = Field(default_factory=LoggingCfg)

    @model_validator(mode="after")
    def _refusals(self) -> RunCfg:
        m, size = self.method, self.model.size_b
        if m.name == "full" and size > FULL_MAX_BILLION_PARAMS and not m.allow_large:
            raise ValueError(f"method full on a {size:g}B model needs far more than one 24 GB GPU (weights, "
                             f"gradients and optimiser state); use qlora, or full on a model of at most "
                             f"{FULL_MAX_BILLION_PARAMS:g}B (method.allow_large overrides)")
        if m.name == "lora" and size > LORA_BF16_MAX_BILLION_PARAMS and self.task == "extract" and not m.allow_large:
            raise ValueError(f"bf16 lora on a {size:g}B vision model with page images does not fit 24 GB; use qlora "
                             "(method.allow_large overrides)")
        if self.task == "decide" and self.data.max_pixels:
            raise ValueError("decide examples are text only: data.max_pixels does not apply")
        if not self.name.replace("-", "").replace("_", "").isalnum():
            raise ValueError("run name: letters, digits, - and _ only (it names the run folder)")
        return self

    @property
    def quantized(self) -> bool:
        return self.method.name in ("qlora", "lora_projector")

    @property
    def merged_serving(self) -> bool:
        """Served as a merged model: vLLM LoRA adapters cannot carry fully trained (non-LoRA) weights."""
        return self.method.name in ("lora_projector", "full")

    @property
    def optimizer(self) -> str:
        return self.train.optim or ("paged_adamw_8bit" if self.quantized else "adamw_torch")


def load_config(path: Path, overrides: dict | None = None) -> RunCfg:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    for dotted, value in (overrides or {}).items():
        node = data
        *parents, leaf = dotted.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = value
    cfg = RunCfg.model_validate(data)
    if not cfg.data.dataset.is_absolute():
        cfg.data.dataset = (Path(path).parent / cfg.data.dataset).resolve() \
            if not cfg.data.dataset.exists() else cfg.data.dataset.resolve()
    return cfg
