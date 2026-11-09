# AAP training pipeline

Fine-tunes the two adapters of [docs/PLAN-MODEL-JSON.md](../docs/PLAN-MODEL-JSON.md): **A** reads the invoice
(pages → printed values), **B** decides the Oracle fields (printed values + context pack → Oracle JSON).
Plan and rules: [docs/PLAN-TRAINING-PIPELINE.md](../docs/PLAN-TRAINING-PIPELINE.md).

This is its own Python project (torch, transformers, peft, bitsandbytes stay out of the application). It
never imports `aap`: the application writes datasets in the `aap-train-chat/1` contract
(`python -m aap.finetune.chat`), this project reads them.

## Setup

```bat
cd training
uv sync                    :: PyTorch with CUDA 12.8 (Blackwell / RTX 5090); add --extra wandb for W&B
uv run python -m aap_train.envcheck
uv run python -m pytest    :: no GPU, no downloads
```

`envcheck` reports the GPU and its compute capability, bf16, a 4-bit layer on the GPU, SDPA /
flash-attention, free GPU memory and free disk. vLLM holds most of the GPU while it runs: stop it before
training (`docker compose stop vllm`) and start it again afterwards.

## A run

```bat
:: 1. a dataset (from the repository root, with the application's environment)
uv run python -m aap.finetune.chat extract --eval-set test-data/invoices-dev-55 --out training/chat-data/smoke-a
uv run python -m aap.finetune.chat decide-smoke --eval-set test-data/invoices-dev-55 --out training/chat-data/smoke-b

:: 2. check config and data, count tokens (loads only the processor)
cd training
uv run python -m aap_train.train --config configs/smoke-a-qlora.yaml --dry-run

:: 3. train; overrides with --set; continue an interrupted run with --resume runs/<run>
uv run python -m aap_train.train --config configs/smoke-a-qlora.yaml --set train.max_steps=20

:: 4. score a checkpoint on dev, package the best one for vLLM
uv run python -m aap_train.evaluate --run runs/<run>
uv run python -m aap_train.package --run runs/<run>
```

| Config | What | Notes |
|---|---|---|
| `smoke-tiny.yaml` | Every step with a tiny random Qwen2.5-VL | Seconds; vLLM can keep running; scores meaningless |
| `smoke-a-qlora.yaml` | Adapter A, QLoRA, 7B, synthetic invoices | Plumbing, memory and speed on the real base |
| `smoke-b-qlora.yaml` | Adapter B, QLoRA, 7B, made-up context pack | Plumbing |
| `a-qlora.yaml`, `b-qlora.yaml` | The real runs | Datasets under `test-data-kk/` (client data) |
| `a-lora-projector.yaml` | A with the vision-language merger trained | Served as a merged model |
| `full-3b.yaml` | Full fine-tune of the 3B base | Full on the 7B is refused (does not fit) |

## Methods

| `method.name` | Base | Trains | Served as |
|---|---|---|---|
| `qlora` (default) | 4-bit NF4 (vision tower bf16) | LoRA on the language model | vLLM LoRA adapter (`adapter/`) |
| `lora` | bf16 | LoRA on the language model | vLLM LoRA adapter (refused for a 7B with page images: does not fit 24 GB) |
| `lora_projector` | 4-bit NF4 | LoRA + the vision-language merger | Merged model (`model/`) |
| `full` | bf16, at most 4B parameters | The language model (vision frozen) | Full checkpoint (`model/`) |

## Run folder

`runs/<name>-<UTC time>/`: `config.yaml`, `run.json` (dataset fingerprint and counts, git commit,
environment, trainable parameters, examples left out as too long), `metrics.jsonl` (loss, learning rate,
peak GPU memory, dev scores), `checkpoints/`, `best/` + `best.json` (the checkpoint with the best dev
score), `final/`, and after packaging `adapter/` or `model/`.

The dev score (`aap_train.metrics`) only chooses the checkpoint. Whether an adapter is used is decided by
the application's evaluation of the served model (`python -m aap.eval`, pipeline 1).

## Rules

- Runs on client data are client data: `runs/` and `chat-data/` are git-ignored; client datasets live under
  `test-data-kk/`. Everything stays on the project machine.
- W&B is optional (`logging.wandb: true`, `uv sync --extra wandb`): metrics and the config only, offline
  unless `logging.wandb_mode: online`; no tables, predictions, images, code or model artifacts.
- No training on client invoices before the written approval (J-1) and the decision rule (CLIENT-DATA P7).
- A smoke run proves the pipeline works; its scores say nothing about client invoices.
