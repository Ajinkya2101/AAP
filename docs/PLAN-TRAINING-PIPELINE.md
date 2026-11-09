# Plan: the training pipeline (pipeline 2)

| Item | Value |
|---|---|
| Status | Approved to build 2026-11-09; T0 first |
| Scope | Everything needed to fine-tune, check and serve adapter A (reading) and adapter B (Oracle fields) of [PLAN-MODEL-JSON.md](PLAN-MODEL-JSON.md), ready before the real training data exists |
| Where | `training/` at the repository root: its own Python project, its own container. The application (`src/aap`) writes the datasets; `training/` reads them and never imports `aap` |
| Builds on | CLIENT-DATA P6-P7, [FINETUNING-SCOPE.md](FINETUNING-SCOPE.md), `aap.finetune.export` |
| Not in scope | Deciding to train on client data (CLIENT-DATA P7 decision rule; J-1 approval). Until then everything runs on synthetic invoices |

Legend: ✅ done · 🟠 in progress · ⬜ not started · ⏸ waiting

## 1. What it has to do

```
 aap (application side)                         training/ (this plan)
 ───────────────────────                        ─────────────────────────────────────────────
 aap.finetune.chat                              aap_train.envcheck   GPU, CUDA, bf16, 4-bit, attention
   synthetic eval sets ─┐                       aap_train.train      config → adapter (or merged model)
   kk-train export ─────┼─► chat dataset ─────► aap_train.evaluate   dev split: field accuracy per checkpoint
   context packs (B) ───┘   (contract §3)       aap_train.package    adapter for vLLM, or merged weights
                                                         │
                                                         ▼
                              vLLM serves base + adapters (multi-LoRA)  ──►  pipeline 1 (aap.eval) measures
```

The two sides meet only at the dataset contract (§3) and at the served model name. That keeps torch,
peft and bitsandbytes out of the application image, and keeps the training code from drifting away
from what production sends.

## 2. Hardware and what fits

| Item | Value | Consequence |
|---|---|---|
| GPU | RTX 5090 Laptop, 24 GB, Blackwell (sm_120) | PyTorch with CUDA 12.8; flash-attention 2 may not build: SDPA attention by default |
| GPU sharing | vLLM uses about 85 % of it while running | vLLM is stopped while training; `aap_train.envcheck` refuses to start when another process holds most of the memory |
| Docker VM memory | 24 GB (`.wslconfig`) | Load shards with `low_cpu_mem_usage`; 4-bit quantization at load time |
| Laptop power | Power mode and thermals throttle long runs | Checkpoints every N steps, resume from the last one |

| Method | Base | Memory estimate (batch 1, gradient checkpointing, one page) | Served as |
|---|---|---|---|
| `qlora` (default) | Qwen2.5-VL-7B, 4-bit NF4 | about 10-14 GB | LoRA adapter on the vLLM base |
| `lora` | 7B bf16 | about 20-24 GB: likely does not fit with page images | LoRA adapter |
| `lora` | Qwen2.5-VL-3B bf16 | about 10-12 GB | LoRA adapter on a 3B base |
| `lora_projector` | 7B 4-bit + the vision-language merger trained in full | about 12-16 GB | **Merged model** (vLLM LoRA cannot carry non-LoRA weights) |
| `full` | Qwen2.5-VL-3B only | about 20+ GB with 8-bit Adam; may not fit | Full checkpoint |
| `full` | 7B | over 100 GB | Not supported here (refused by the config check) |

The estimates are to be replaced by measured peaks from T0/T2 (written into the run directory).

**Serving the result.** vLLM runs the base model fp8-quantized. A LoRA adapter trained on a 4-bit base and
served on an fp8 base is normal practice, but the small difference is measured, not assumed: every
adapter is checked through pipeline 1 on the served model, not on the training process.

## 3. Dataset contract: `aap-train-chat/1`

One folder per dataset, written by the application side:

```
<dataset>/
  dataset.json        contract version, task (extract | decide), source, created, counts per split,
                      prompt version, image settings (max_pixels, DPI), sha256 of every jsonl file
  train.jsonl         one example per line
  dev.jsonl
  test.jsonl          (optional; never read by training, only listed)
  images/<example id>/<page>.png
```

Each line:

```json
{
  "id": "…",                       // stable; the document's sha256 or invoice id + chunk
  "task": "extract",               // or "decide"
  "messages": [
    {"role": "system", "content": [{"type": "text", "text": "…"}]},
    {"role": "user",   "content": [{"type": "text", "text": "Page 1 image:"},
                                   {"type": "image", "path": "images/<id>/1.png"},
                                   {"type": "text", "text": "Text layer of page 1: …"}]}
  ],
  "target": "{…}",                 // the assistant answer: JSON text in the production schema and key order
  "schema": {…},                   // the JSON schema production sends with the request (kept for checks)
  "meta": {"split": "train", "label": "corrected|approved|synthetic|keyed", "supplier": "…", "scanned": false}
}
```

Rules:

- **Built by the same code as production.** Adapter A's messages come from the function that builds the
  extraction request (`aap.extraction.extract`), adapter B's from the context-pack builder. Only the image
  is stored as a file instead of base64.
- `target` is serialised with the production key order. Training loss is computed on the target tokens only.
- A dataset is never changed after it is written; a new build is a new folder.
- Synthetic and client datasets carry `source` in `dataset.json`; a client dataset lives under
  `test-data-kk/` (git-ignored), never under `training/`.

**Adapter B's input (`task = decide`).** The user message holds two JSON blocks: the printed values (what
adapter A, or the base model, read) and the context pack (PLAN-MODEL-JSON §4). The target is the Oracle
fields in the Oracle-ready JSON's shape, with the `basis` of each value. The context-pack builder does not
exist yet, so `decide` datasets are versioned separately (`context_pack_version` in `dataset.json`) and
the smoke dataset uses a made-up context pack with the same keys.

## 4. The `training/` project

```
training/
  README.md                 how to run; the rules in §8
  pyproject.toml            own uv project: torch (cu128), transformers, peft, accelerate, bitsandbytes,
                            pillow, pyyaml, pydantic; extra "wandb"; dev: pytest, ruff
  Dockerfile                PyTorch CUDA 12.8 base; the project installed; no application code
  configs/
    smoke-a-qlora.yaml      synthetic, few steps: plumbing only
    smoke-b-qlora.yaml
    a-qlora.yaml            adapter A on the real train split (when allowed)
    b-qlora.yaml
    a-lora-projector.yaml
    full-3b.yaml
  src/aap_train/
    config.py               the run config (pydantic): model, method, data, training, eval, logging; checks
                            that refuse impossible combinations (full on 7B, lora_projector without merge)
    data.py                 contract reader: validates dataset.json and hashes, loads examples, images
    collate.py              chat template → tokens; labels masked to the target only; image processor with
                            the dataset's max_pixels
    methods.py              method → model loading (4-bit or bf16), PEFT config, trainable modules
    train.py                CLI: python -m aap_train.train --config configs/x.yaml [--resume]
    evaluate.py             generate on dev, parse JSON, field accuracy; best checkpoint by that score
    metrics.py              field accuracy without the application (header fields, lines, decide fields)
    package.py              adapter folder for vLLM (adapter_config.json + weights), or a merged model
    envcheck.py             GPU, CUDA, bf16, 4-bit load, attention backend, free memory, disk
    tracking.py             run directory, metrics.jsonl; optional wandb with the rules in §8
  tests/                    no GPU, no downloads: config checks, contract reader, label masking,
                            method → PEFT mapping, metrics, package layout
  runs/                     git-ignored: one folder per run
```

**Run directory** `training/runs/<name>-<UTC time>/`: the resolved config, dataset hash, git commit,
environment report (envcheck), `metrics.jsonl` (loss, learning rate, dev scores, peak GPU memory),
checkpoints, the chosen checkpoint and the packaged adapter. Runs on client data are client data.

## 5. Training defaults

| Setting | Adapter A (reading) | Adapter B (decide) |
|---|---|---|
| Base | Qwen2.5-VL-7B-Instruct, the served revision | The same base, text-only input |
| Method | `qlora` | `qlora` |
| LoRA | rank 16, alpha 32, dropout 0.05; language model attention + MLP projections; vision tower frozen | rank 16, alpha 32; same modules |
| Sequence | Up to 8,192 tokens (one page ≈ 1,280 image tokens at the served max_pixels, plus text layer and answer) | Up to 4,096 tokens |
| Batch | 1, gradient accumulation 8 | 2, accumulation 4 |
| Learning rate | 1e-4, cosine, 3 % warm-up | 2e-4 |
| Epochs | 2-3, best checkpoint on dev | 3 |
| Precision | bf16 compute | bf16 compute |
| Checkpoints | Every 50 steps, keep 3 | Every 50 steps |
| Seed | Fixed, written to the run | Fixed |

Defaults are starting points for the real runs; the smoke configs use a handful of steps.

## 6. Evaluation inside training, and outside

- **Inside (choosing the checkpoint):** `aap_train.evaluate` generates on the dev split with the checkpoint,
  parses the JSON and scores it field by field: header fields, line F1 (adapter A), Oracle fields equal to
  the target (adapter B). The best checkpoint by that score is kept, not the lowest loss.
- **Outside (deciding to use it):** the packaged adapter is served by vLLM and measured by pipeline 1
  (`aap.eval` on the test split, plus the synthetic `invoices-dev-55` regression). Only that result counts,
  because it is the served model with the production prompt, decoding and checks.

## 7. Serving

- vLLM gets `--enable-lora`, `--max-lora-rank 16` and the adapters as named modules (for example
  `qwen2.5-vl-7b-kk-read` and `qwen2.5-vl-7b-kk-decide`); the application picks them with `EXTRACT_MODEL`
  and a new `DECIDE_MODEL`. Rollback: point the setting back to the base model's name.
- Two things are verified before the real runs (T3), not assumed: LoRA on the fp8-quantized Qwen2.5-VL
  in the vLLM version we use, and text-only requests with adapter B on the same served base.
- A merged model (`lora_projector`, `full`) is served as its own vLLM model instead of an adapter.

## 8. Rules

- **Client data:** datasets from client invoices stay under `test-data-kk/`; runs, checkpoints and adapters
  trained on them stay under `training/runs/` (git-ignored) on the project machine.
- **wandb** is optional (`logging.wandb: true`). When on: metrics and the config only; no tables, sample
  predictions, images, datasets or model artifacts; `WANDB_LOG_MODEL=false`, `WANDB_DISABLE_CODE=true`;
  default mode `offline` (sync by hand), `online` only by choice. The run name never contains a supplier name.
- **No training on client invoices** before the J-1 approval and the CLIENT-DATA P7 decision rule.
- **Smoke results are not results.** A run on synthetic invoices proves the pipeline works end to end; its
  scores say nothing about Krispy Kreme invoices.

## 9. Work items

| # | Item | Status |
|---|---|---|
| T0 | `training/` project skeleton, `envcheck` (GPU, CUDA, bf16, 4-bit, SDPA, free memory); Dockerfile; run it on the RTX 5090 | ⬜ |
| T1 | Dataset contract: application-side builder `aap.finetune.chat` for adapter A from a synthetic eval set and from the `aap.finetune.export` output; the extraction request factored out of `extract()` so both use one function | ⬜ |
| T2 | `aap_train`: config, contract reader, collator with target-only labels, methods (`qlora`, `lora`, `lora_projector`, `full` with the refusals), trainer, run directory, optional wandb; unit tests without GPU | ⬜ |
| T3 | Smoke run A on synthetic invoices (QLoRA, 7B); measured memory and speed; `evaluate` picks the checkpoint; `package` writes the adapter | ⬜ |
| T4 | Adapter B contract with a made-up context pack; smoke dataset and smoke run B | ⬜ |
| T5 | vLLM: `--enable-lora` behind a compose setting, two adapters, `EXTRACT_MODEL` / `DECIDE_MODEL`, rollback; verify fp8 base + LoRA and text-only B | ⬜ |
| T6 | `lora_projector` merge and `full` on 3B, smoke only | ⬜ |
| T7 | Real runs: B on the train split (learning curve), A only if the decision rule says so | ⏸ data, J-1 |

Order: T0 → T1 → T2 → T3 → T4 → T5 → T6. If T0 shows that 4-bit loading does not work on this GPU,
`lora` on the 3B base becomes the default and §2 is updated before T2.
