# AAP training pipeline

Fine-tunes the two adapters of [docs/PLAN-MODEL-JSON.md](../docs/PLAN-MODEL-JSON.md): **A** reads the invoice
(pages → printed values), **B** decides the Oracle fields (printed values + context pack → Oracle JSON).
Plan and rules: [docs/PLAN-TRAINING-PIPELINE.md](../docs/PLAN-TRAINING-PIPELINE.md).

This is its own Python project (torch, transformers, peft, bitsandbytes stay out of the application). It
never imports `aap`: the application writes datasets in the `aap-train-chat/1` contract
(`python -m aap.finetune.chat`), this project reads them.

## The training container (use this)

Training runs in its own container, `aap-train:dev` (compose service `train`, profile `train`), never in
the application stack and not on the host: Windows Application Control blocks torch in `training/.venv`
(2026-11-23). `train.bat` at the repository root wraps it. Commands marked GPU stop vLLM first and start
it again when the job ends, even when it fails (if you stop a job with Ctrl+C and answer Y, start vLLM
yourself: `docker compose --profile gpu start vllm`). Paths are relative to `training\`.

```bat
:: once, and after a change to pyproject.toml / uv.lock (code changes need no rebuild: the source is mounted)
train.bat build
train.bat check                                   :: GPU sm_120, bf16, 4-bit, free GPU memory, disk
train.bat test                                    :: unit tests, no GPU
train.bat smoke                                   :: tiny random model, every step, seconds; vLLM keeps running

:: adapter B (client data under test-data-kk\, written by aap.finetune.chat decide)
train.bat dry configs/b-qlora.yaml                :: dataset contract, token counts, nothing too long
train.bat run configs/probe-b-qlora.yaml          :: GPU: 7B at the real lengths on made-up data, about 9 minutes
train.bat run configs/b-qlora.yaml                :: GPU: the real run, 2 epochs, about 4.5 h (after the green light)
train.bat run configs/b-qlora.yaml --set data.dataset=../test-data-kk/decide-b-2026-11-23-real --set name=b-qlora-real
train.bat resume runs/b-qlora-<time>              :: GPU: continue an interrupted run
train.bat eval runs/b-qlora-<time> --checkpoint base   :: GPU: dev loss without the adapter, to compare
train.bat package runs/b-qlora-<time> --slot decide    :: GPU: install the best checkpoint for vLLM
```

The base weights come from the stack's `models` volume (the weights vLLM serves): a config naming
`Qwen/Qwen2.5-VL-7B-Instruct` is read from `/models/Qwen2.5-VL-7B-Instruct` (`AAP_TRAIN_MODEL_DIR`); no
download. Runs land in `training\runs\` on the host.

## Setup on the host (not used for runs)

```bat
cd training
uv sync                    :: PyTorch with CUDA 12.8 (Blackwell / RTX 5090); add --extra wandb for W&B
uv run python -m aap_train.envcheck
uv run python -m pytest    :: no GPU, no downloads; AAP_TRAIN_GPU_TESTS=1 adds the tiny-model end-to-end runs
```

`envcheck` reports the GPU and its compute capability, bf16, a 4-bit layer on the GPU, SDPA /
flash-attention, free GPU memory and free disk. vLLM holds most of the GPU while it runs: stop it before
training (`docker compose stop vllm`) and start it again afterwards.

## A run

```bat
:: 1. a dataset (from the repository root, with the application's environment)
uv run python -m aap.finetune.chat extract --eval-set test-data/invoices-dev-55 --out training/chat-data/smoke-a
uv run python -m aap.finetune.chat decide-smoke --eval-set test-data/invoices-dev-55 --out training/chat-data/smoke-b
:: adapter B from a delivery's keyed records (client data: under test-data-kk/, never training/)
uv run python -m aap.finetune.chat decide --from client-data\kk\<earlier> --delivery client-data\kk\<delivery> --out test-data-kk\decide-b

:: 2. check config and data, count tokens (loads only the processor)
cd training
uv run python -m aap_train.train --config configs/smoke-a-qlora.yaml --dry-run

:: 3. train; overrides with --set; continue an interrupted run with --resume runs/<run>
::    the 7B base without a download: copy the weights vLLM uses (24 GB, models/ is git-ignored), once
docker cp aap-vllm-1:/models/Qwen2.5-VL-7B-Instruct models/
uv run python -m aap_train.train --config configs/smoke-a-qlora.yaml --set model.base=models/Qwen2.5-VL-7B-Instruct

:: 4. score a checkpoint on dev (--checkpoint base = no adapter, to compare), package the best one
uv run python -m aap_train.evaluate --run runs/<run> [--checkpoint base] [--mode loss|generate]
uv run python -m aap_train.package --run runs/<run> --slot read      :: adapter A; decide for adapter B
```

## Serving the adapters

```bat
:: from the repository root: vLLM with the base and both slots (training/adapters/read and /decide)
docker compose -f docker-compose.yml -f docker-compose.lora.yml --profile gpu up -d --no-deps vllm
:: the api / worker use adapter A with EXTRACT_MODEL=qwen2.5-vl-7b-kk-read; back to qwen2.5-vl-7b = rollback
:: the default vLLM again (no adapters)
docker compose --profile gpu up -d --no-deps vllm
```

Both slots must hold an adapter for vLLM to start with the override. Verified with vLLM 0.10.1.1 on the
fp8 base: page-image requests to adapter A and text-only requests to adapter B, schema enforced.

## Measured on the RTX 5090 (QLoRA, 7B, 2026-11-09; adapter B at real lengths 2026-11-23, in the container)

| | Adapter A (pages) | Adapter B (text) |
|---|---|---|
| Tokens per example | 3.2k-7.3k (one page ≈ 1,240 image tokens) | smoke: about 500; real packs (2026-11-23): 1.5k-4.2k, median 2.8k |
| Peak GPU memory | 19.9 GB | smoke 13.3 GB; real lengths 21.0 GB at batch 1 (31 GB at batch 2: spills, half speed) |
| Speed | about 10 s per invoice (350 invoices × 3 epochs ≈ 3 hours) | smoke 0.7 s; real lengths about 4 s per example (2,000 × 1 epoch ≈ 2.2 h) |
| Dev scoring (loss) | 3.5 s per invoice | smoke 0.2 s; real lengths 1.5 s per example |

vLLM holds about 15 GB while it runs: stop it for training (`docker compose --profile gpu stop vllm`).

| Config | What | Notes |
|---|---|---|
| `smoke-tiny.yaml` | Every step with a tiny random Qwen2.5-VL | Seconds; vLLM can keep running; scores meaningless |
| `smoke-a-qlora.yaml` | Adapter A, QLoRA, 7B, synthetic invoices | Plumbing, memory and speed on the real base |
| `smoke-b-qlora.yaml` | Adapter B, QLoRA, 7B, made-up context pack | Plumbing |
| `probe-b-qlora.yaml` | Adapter B at the real sequence lengths, made-up data (`chat-data/probe-b`) | Memory and speed before a real run |
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

The dev score only chooses the checkpoint. `eval.mode: loss` (default) is the loss on the answer tokens.
`eval.mode: generate` decodes freely and scores field by field (`aap_train.metrics`); free decoding is not
what vLLM serves (it enforces the JSON schema), so a model that invents keys scores 0 there and answers
fine when served. Whether an adapter is used is decided by the application's evaluation of the served
model (`python -m aap.eval`, pipeline 1).

## Rules

- Runs on client data are client data: `runs/` and `chat-data/` are git-ignored; client datasets live under
  `test-data-kk/`. Everything stays on the project machine.
- W&B is optional (`logging.wandb: true`, `uv sync --extra wandb`): metrics and the config only, offline
  unless `logging.wandb_mode: online`; no tables, predictions, images, code or model artifacts.
- No training on client invoices before the written approval (J-1) and the decision rule (CLIENT-DATA P7).
- A smoke run proves the pipeline works; its scores say nothing about client invoices.

## Green light for the first real run (adapter B)

Start `train.bat run configs/b-qlora.yaml` (or the HPO) only when every line holds:

| # | Condition | How to check | 2026-11-23 |
|---|---|---|---|
| G1 | Written approval to train on the client's records (J-1, Jayesh / Siva) | the email | ❌ open: asked in the 11-20 email |
| G2 | The image builds and the GPU checks pass in it | `train.bat build`, `train.bat check` | ✅ |
| G3 | Unit tests and the tiny end-to-end run pass in the container | `train.bat test`, `train.bat smoke` | ✅ 36 passed; smoke ok |
| G4 | The dataset passes the contract, nothing too long | `train.bat dry configs/b-qlora.yaml` | ✅ 2,000 / 52, 0 too long at 5,120 |
| G5 | The 7B fits and runs at the real lengths without client data | `train.bat run configs/probe-b-qlora.yaml` | ✅ 21.0 GB peak, about 4 s per example |
| G6 | The floor to beat is recorded | `aap.decide.evaluate --split dev` with `LLM_BACKEND=stub` | ✅ dev 59.6 % invoices exact, 66.7 % lines |
| G7 | On AC power, Windows power mode Best performance; nothing else on the GPU; a night free | battery icon; `nvidia-smi` | check before starting |

## HPO (hyperparameter search)

`python -m aap_train.sweep` runs a sweep file from `configs/sweeps/`: a grid (every combination) or a
random search over any key of a run config, one trial after another (one GPU), each trial an ordinary
run in its own process. Results: `runs\sweeps\<sweep>-<time>\leaderboard.md` (ranked by dev loss),
`trials.jsonl` (one line per trial), each trial's run folder next to them.

```bat
train.bat sweep configs/sweeps/b-screen.yaml --dry-run          :: list and validate the trials (no GPU)
train.bat sweep configs/sweeps/b-screen.yaml                    :: GPU: run them all
train.bat sweep configs/sweeps/b-screen.yaml --max-trials 3     :: GPU: only 3 now ...
train.bat resume-sweep runs/sweeps/b-screen-<time>              :: ... the rest later; also after a crash
train.bat report runs/sweeps/b-screen-<time>                    :: the leaderboard
```

Writing a sweep: copy one of `configs/sweeps/` and edit `space`. A value list is a dimension; a list of
dicts sets several keys together (`lora: [{method.rank: 8, method.alpha: 16}, ...]`); `fixed` applies to
every trial; random sweeps also take `{loguniform: [lo, hi]}`, `{uniform: [lo, hi]}`, `{int: [lo, hi]}`.
A single run is tried by hand with `--set`: `train.bat run configs/b-qlora.yaml --set train.learning_rate=3e-4 --set name=b-lr3`.

**For adapter B, in this order** (measured 2026-11-23 on the RTX 5090 with a made-up probe at the real
lengths: about 4 s per example per epoch at batch 1, plus about 1.5 s per dev example at each save; the full
set is 2,000 examples, about 2.2 h per epoch; the real-only control 382, about 25 min):

| Stage | Sweep | What | Trials | Time |
|---|---|---|---|---|
| 1 | `b-screen.yaml` | learning rate {1e-4, 2e-4, 4e-4} × LoRA rank {8, 16, 32}, 600 examples, 1 epoch | 9 | about 7 h (a night) |
| 2 | `b-refine.yaml` | around the winner, 1,000 examples: epochs {1, 2}, dropout, warmup, a narrower learning rate | 4 | about 7 h (a night) |
| 3 | `b-data.yaml` | the winner on the full set vs the real-only control, 2 seeds each, 2 epochs | 4 | about 11 h (two nights: `--max-trials 2`, then `resume-sweep`) |

Stages 2 and 3 have `<-` marks: put the previous stage's winner there first. What matters, roughly in
order: learning rate, epochs (B overfits quickly: the made-up probe's training loss fell from 0.6 to 0.001
within 12 steps; watch the dev loss rise after its best step in `metrics.jsonl`), LoRA rank (8-32;
alpha = 2 × rank), then dropout and warmup. Batch size stays 1 (batch 2 needs 31 GB at these lengths and
spills into system memory at half the speed), grad_accum 8, max_seq_len 5120 (the longest example is 4,240
tokens).

**How to read the result.** The dev loss ranks trials; it is not the decision. With 52 real dev invoices a
difference of a few thousandths is noise: look at the seed spread in stage 3 before believing one.
Then, for the best 1-2 trials: `train.bat package <run> --slot decide`, serve with the LoRA override,
and score with the application on dev (`aap.decide.evaluate --split dev --model qwen2.5-vl-7b-kk-decide`,
section below); it must beat the floor (dev: 59.6 % invoices exact, 66.7 % lines, 2026-11-23) and the
untrained base (S1). Test is scored once, for the chosen adapter, at the end, never to pick a trial.

## Adapter B: offline score and learning curve

```bat
:: from the repository root; LLM_BACKEND=stub gives the floor (site defaults + most used account, no model)
uv run python -m aap.decide.evaluate --dataset test-data-kk\decide-b --split dev --model qwen2.5-vl-7b-kk-decide
:: 25 / 50 / 100 % of train: stop vLLM, train, package --slot decide, serve with LoRA, score; --dry-run lists the steps
uv run python -m aap.decide.curve --dataset test-data-kk\decide-b --base-model models/Qwen2.5-VL-7B-Instruct
```

Both append a line to `runs/eval-runs.jsonl` (headline numbers only). Training on client data waits for the J-1
approval (rule above).
