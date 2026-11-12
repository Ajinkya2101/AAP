# Plan: one model that returns the Oracle-ready JSON

| Item | Value |
|---|---|
| Status | Proposed 2026-11-09. Everything that needs no invoice files is built (2026-11-12): E1-E5, T1-T3 and B's dataset from the keyed records; E6 and T4-T5 wait for the PDFs and the J-1 approval |
| Why | Management expects a model that takes an invoice and returns the EBS-ready JSON, not a chain of lookups |
| Constraint | Few examples: several hundred keyed invoices from about 18 suppliers, a few weeks of history (more requested) |
| Target | Mid-December 2026, non-PO ([PLAN-MID-DECEMBER.md](PLAN-MID-DECEMBER.md)) |
| Builds on | [IMPLEMENTATION-PLAN-CLIENT-DATA.md](IMPLEMENTATION-PLAN-CLIENT-DATA.md) P4-P9, [FINETUNING-SCOPE.md](FINETUNING-SCOPE.md), [ORACLE-JSON.md](ORACLE-JSON.md) |

Legend: ✅ done · 🟠 in progress · ⬜ not started · ⏸ waiting on data

## 1. The problem

Two goals conflict:

- **What is expected:** invoice in, one model, Oracle-ready JSON out. This includes the fields that are not
  printed: GL account, distribution set, invoice type, terms, pay group, payment method, liability account.
- **What the data allows:** a model cannot learn the GL accounts, supplier IDs and term IDs from a few hundred
  invoices by memorising them. Even with more data, memorised Oracle values go stale: an account is disabled
  or a supplier site changes, and the model keeps writing the old value until it is retrained
  (FINETUNING-SCOPE, criterion 3).

## 2. The idea: the model writes the whole JSON, the code gives it the facts and checks the result

The model is not asked to remember Oracle. For each invoice the code hands it the facts it needs, and the
model's job becomes **reading the invoice and choosing among the facts**, which a few hundred examples can
teach.

```
 invoice (pages, text layer)
        │
        ▼
 ┌──────────────┐   printed values    ┌──────────────────────────┐
 │ reading      │ ──────────────────► │ context pack (code)      │  supplier/site defaults, the supplier's
 │ (adapter A)  │                     │ "as of" the invoice date │  accounts and distribution sets with how
 └──────────────┘                     └────────────┬─────────────┘  often each was used, recent keyed invoices
                                                   ▼                of the supplier, the instance's lists
                                      ┌──────────────────────────┐
                                      │ Oracle fields            │  writes the JSON; every Oracle value is
                                      │ (adapter B)              │  restricted to the candidates (schema enum)
                                      └────────────┬─────────────┘
                                                   ▼
                                      ┌──────────────────────────┐
                                      │ checks (code, as today)  │  V-LOV, ccid_valid, site defaults,
                                      └────────────┬─────────────┘  totals; disagreement → review
                                                   ▼
                                          Oracle-ready JSON
```

Three things make it safe with little data:

1. **Context instead of memory.** The supplier's accounts, defaults and lists come from the reference copy at
   request time. When Oracle changes, the next reference sync changes the context; no retraining.
2. **Constrained output.** The JSON schema sent with each request (`response_format` json_schema, strict, as in
   `llm/client.py`) lists the allowed values for that invoice: the supplier's candidate accounts, its
   distribution sets, the enabled invoice and line types. The model cannot write an account that is not a
   candidate.
3. **The checks stay.** The model also writes the site-default fields (terms, pay group, payment method,
   liability account); the code compares them with the site, and a difference goes to review instead of
   being trusted. So management gets "the model writes everything", and Oracle gets the same guarantees as
   today.

**Labels come free for the Oracle part.** The training target for adapter B is what AP keyed, which is
exactly what Dhivya's extracts contain. Only the reading part (adapter A) needs the printed-value labels
from the review pass (CLIENT-DATA P6).

## 3. Two adapters behind one endpoint, or one joint adapter

| | Option T: two adapters (recommended) | Option J: one joint adapter |
|---|---|---|
| Calls | A reads (pages → printed JSON), B decides (printed JSON + context → Oracle JSON) | One call: pages + context → printed and Oracle JSON |
| Base | The same Qwen2.5-VL-7B; B runs text-only on it; vLLM serves both adapters (multi-LoRA) | Qwen2.5-VL-7B |
| Data needed | A: printed labels (P6). B: keyed records, no labelling | Both labels on the same invoices |
| Training cost | B is short text; trains in minutes per epoch | Every step carries the page images and the context |
| Context size | No images in B's input | Images and the context share one window |
| Finding the cause of a miss | Separate: reading error (A) or choice error (B) | Mixed; needs the per-field `basis` (§5) |
| What management sees | One endpoint, one model, one JSON | The same |

Both are "one model" in the sense that matters: one base model, fine-tuned for Krispy Kreme, one request
per invoice from the caller's side. T is the primary path. J is built only as an experiment if T works and
time allows (§8).

## 4. The context pack

Built by code for each invoice, from `aap_kk`, **as of the invoice's GL date**: nothing keyed on or after it,
and never the invoice itself. Without this rule the training and test examples would contain their own
answer.

| Part | Content | Limit |
|---|---|---|
| Site | Supplier number, site code, OU, terms, pay group, payment method, liability account, distribution set, currency | 1 site |
| Accounts | The supplier's accounts used before the date: account string, segment descriptions, share of amount, last used, typical line text | Top 15 by amount |
| Distribution sets | Sets the supplier used, with their lines | All of the supplier's |
| Recent invoices | The supplier's last keyed invoices: date, amount, lines with account | Last 5 |
| Lists | Enabled invoice types, line types, sources | From V-LOV lookups |
| Store table | Store → cost center, when a store is printed | Matching rows |

When the supplier has no history, the accounts part falls back to the most used accounts with the same
natural account across suppliers, and the line goes to review. A new supplier's JSON is never `ready` on a
guess.

## 5. Output

The existing schema ([oracle-invoice.schema.json](oracle-invoice.schema.json), v1.0), plus one field per
decided value: `basis` = `printed` | `site_default` | `history` | `distribution_set` | `chosen`. It
tells the reviewer and the eval where each value came from, so the four causes of a miss (CLIENT-DATA P5)
stay separable even when one model writes everything.

## 6. Pipeline 1: evaluation

The existing harness (`aap.eval`, gold set, shadow comparison, GL metrics), extended. It runs the
production code, so what is measured is what ships.

**Systems compared on the same test split:**

| System | What it is | Why |
|---|---|---|
| S0 | Today's pipeline: extraction, rules, model as tie-breaker | The baseline to beat |
| S1 | Base model + context pack, no training | Shows how much the context alone gives |
| S2 | Adapter B (and A if trained) + context pack | The proposal |
| S3 | Joint adapter (option J) | Only if time allows |

**Metrics, per supplier, for held-out suppliers separately, and in total:**

- share of invoices whose JSON equals the keyed record (shadow comparison);
- GL: amount-weighted, full combination and natural account;
- share `ready` and the accuracy of the `ready` ones (must be ≥ 98 %);
- site-default fields: how often the model disagrees with the site, and who was right;
- printed fields: the existing header gate (≥ 97 %), line F1, invented values.

**Learning curve.** S2 trained on 25 %, 50 % and 100 % of the train split. If accuracy is still rising at
100 %, more data will help, and the 12 months of history asked of Dhivya is worth waiting for. This is
the evidence for the "too few examples" discussion, instead of an opinion.

**Rules:** prompt and context changes on the dev split only; the test split is scored once per system;
the split is fixed before the first run and never changed.

| # | Item | Needs PDFs | Status |
|---|---|---|---|
| E1 | Intake reads the 2026-11-09 delivery format (sheet names, joined supplier view) | No | ✅ `aap.clientdata.views` (f97c8c8) |
| E2 | Context pack builder with the "as of" rule; unit tests on fixtures | No | ✅ `aap.decide.context` / `history`: from a delivery or from the application database (identical packs, integration test); `leaks()`; `gl_history_line` carries site, invoice, GL date and set (migration 0006). On the 11-09 delivery: 493 October packs, 0 leaks, 15 without history, about 1.6k-2.3k tokens |
| E3 | Per-invoice schema with candidate enums; `basis` field | No | ✅ `aap.decide.schema` / `request` (prompt `decide/1`); node `agents/nodes/decide.py` after GL coding, `DECIDE_MODE` off / shadow / apply: in apply the model's line accounts are used and every difference from the code goes to review, header values stay the code's (difference = `MODEL_DIFFERS`) |
| E4 | S0 and S1 runnable from the harness (`--system s0|s1|s2`) | No (smoke on synthetic) | ✅ `python -m aap.eval --system`; metrics and report show how B's answers compare with the code; smoke on the replica fixture (s0 and s1 on one split, nothing wrong without review) |
| E5 | Learning-curve runner; run log with model, adapter, prompt, split and dataset hash | No | ✅ `runs/eval-runs.jsonl` (git-ignored, headline numbers only); `python -m aap.decide.evaluate` (B on a split, offline); `python -m aap.decide.curve` (stub and S1 rows, then train / package / serve / score per fraction; `--dry-run`) |
| E6 | Gold set from the real delivery; S0 and S1 on dev, then test | Yes | ⏸ PDFs (D-1) |

## 7. Pipeline 2: fine-tuning

| Part | Choice |
|---|---|
| Datasets | A: `aap.finetune.export` (`kk-train` source, split field). B: built from the keyed records + context pack + A's reading of the same invoice (so B learns on the input it will really get, errors included) |
| Training data rule | Train split only; context built as of each invoice's date; test and dev invoices never in a context pack used for training |
| Method switch | `qlora` (default), `lora` (bf16, if memory allows), `lora_projector` (LoRA + the vision-language projector, for A when scans are the problem), `full` (only for a small model such as Qwen2.5-VL-3B; a full fine-tune of the 7B needs well over 100 GB) |
| Loss | On the assistant's JSON only |
| Checkpoint choice | Field accuracy on dev (B: JSON equal to keyed record; A: header gate), not training loss |
| Tooling | TRL or Unsloth; separate `train` container (CLIENT-DATA P7.1); vLLM stopped while training |
| Monitoring | Weights & Biases optional: **metrics and configs only**; no sample predictions, tables, images or model artifacts (client data), `log_model` off; offline mode or a self-hosted server if the client prefers |
| Reproducibility | Seed, dataset hash, prompt version, context-pack version and base model revision in the run config |
| Serving | vLLM multi-LoRA (`--enable-lora`); adapters registered by name; `EXTRACT_MODEL` / new `DECIDE_MODEL` settings; rollback = point back to the base model |
| Check before use | Pipeline 1 on test; synthetic `invoices-dev-55` regression |

| # | Item | Needs PDFs | Status |
|---|---|---|---|
| T1 | `train` container; `aap.finetune.train` with the method switch; smoke run on synthetic invoices (plumbing only, says nothing about accuracy) | No | ✅ `training/` (PLAN-TRAINING-PIPELINE T0-T3) |
| T2 | Dataset builder for B; smoke run on the keyed history alone, without pages (plumbing only) | No | ✅ builder: `python -m aap.finetune.chat decide` (gold set's split, no test / dev invoice in any pack, nested learning-curve subsets). On the 11-09 delivery: 653 examples (490 / 66 / 97), 1.5k-3.5k tokens; `aap_train` dry run accepts it. The smoke run on it is training on client data: ⏸ J-1 |
| T3 | vLLM serving two adapters on one base; switch and rollback tested; confirm text-only requests to the VL base work with B | No | ✅ PLAN-TRAINING-PIPELINE T5 (2026-11-09); `DECIDE_MODEL` / `DECIDE_ADAPTER_MODEL` (2026-11-12) |
| T4 | Train B on the real train split; learning curve | Yes | ⏸ J-1 (the keyed records are enough for B; PDFs only for A's reading in its input) |
| T5 | Train A, only if the reading errors miss the gate after prompt work (CLIENT-DATA P7 rule) | Yes + P6 labels | ⏸ |

**Floor without training (2026-11-12).** `aap.decide.evaluate` with the stub (site defaults and the
supplier's most used account, no model) on B's test split of the 11-09 delivery: 69 % of invoices exact,
80 % of lines (89 % by amount), terms 99 %. S1 and S2 have to beat this.

**Synthetic examples for B (optional).** `tools/invoicegen` can render invoices from the keyed records to
give B more examples of "this content → this account". They may be used for B's training only, never for
A, the gold set or any evaluation, and they are marked in the dataset so that a run with and without them
can be compared.

## 8. Schedule

| Week | Work | Depends on |
|---|---|---|
| Nov 9-13 | E1-E3, T1 | — |
| Nov 16-20 | E4-E5, T2-T3; E6 as soon as the first PDFs arrive (S0, S1 on dev) | PDFs |
| Nov 23-27 | T4 (B), learning curve; P6 labelling starts for A | PDFs, J-1 approval |
| Nov 30-Dec 4 | S0 / S1 / S2 on test; T5 only if the rule says so; S3 only if S2 is done | T4 |
| Dec 7-11 | The winning system behind the one endpoint; demo on held-out invoices | — |

If the PDFs are late, T5 and S3 drop first; S1 (context, no training) needs no training data at all and is
the fallback that still meets "one model writes the JSON".

## 9. What we decide, and what we tell management

- **Ship the system that wins on the test split**, S2 if it beats S0 and S1, otherwise S1, otherwise S0. All
  three return the same JSON through the same endpoint.
- **How to describe it:** one model, fine-tuned for Krispy Kreme, reads the invoice and writes the Oracle JSON;
  it is given the supplier's setup and history for each invoice, and its output is checked against Oracle's
  rules before anything is created. That is accurate for S1 and S2, and the check is a control auditors ask
  for, not a weakness.
- **With the learning curve,** the size question is answered with a measurement: what accuracy the current
  data gives and whether more history would raise it.

## 10. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Context leaks the answer (the invoice's own or later coding) | Results look far better than they are | "As of" rule, tested (E2); test invoices excluded from history (CLIENT-DATA P2) |
| Few invoices for the suppliers that use many accounts | B learns them poorly | Review for those lines; learning curve shows whether more data helps; the request for 12 months of history |
| Line descriptions are mostly empty in the keyed history | History text gives little signal | The account choice reads the printed invoice (A's output), not the keyed text |
| B trained on clean printed values, served A's errors | Worse in production than in training | B's training input is A's real reading (§7) |
| Context makes the request too long | Truncation, slow requests | Limits in §4; option T keeps images out of B's input |
| A tuned model does worse on unseen suppliers | New suppliers break | Held-out suppliers in the test split; adapter not used where it loses to S1 |
| Client data in outside tools | Compliance | wandb metrics only; data, adapters and runs on the project machine (CLIENT-DATA §9) |
