# LLM usage by stage

Every place the application sends a request to an LLM, stage by stage, with the fine-tuning scope of each.
Keep this file current: a new `llm.ask(...)` or `choose(...)` call needs a row here.

Last checked against the code: 2026-10-29 (prompt `v5`). Counts are from the dev database on the same day.

Related: [MODEL-USAGE.md](MODEL-USAGE.md) (the three models and their settings),
[FINETUNING-SCOPE.md](FINETUNING-SCOPE.md) (the full fine-tuning analysis, figures of 2026-10-11).

## How the LLM is called

There are only two ways into the model, both through `ask()` in `src/aap/llm/client.py`:

| Entry point | `task` | Model used | Callers |
|---|---|---|---|
| `extract()` in `src/aap/extraction/extract.py` | `extract`, `extract_taxes` | `EXTRACT_MODEL` | Stage 5 only |
| `choose()` in `src/aap/mapping/choose.py` | `choose` | `CHOICE_MODEL` | Stage 7 (4 places), agents (2 places) |

The model is picked from the task name: a task starting with `extract` goes to `EXTRACT_MODEL`, anything
else to `CHOICE_MODEL`. Both default to `qwen2.5-vl-7b`, served by the one vLLM server (`fp8`).

Rules that hold for every call:

- The answer is JSON constrained by a schema (vLLM structured output). For choices the schema is an enum of
  candidate ids prepared by code, so the model cannot invent an Oracle value.
- No tool definitions are sent. The model never calls Oracle or the database.
- `LLM_TEMPERATURE=0`. One retry when the answer does not parse; no retry when it was cut off at
  `max_tokens`, because at temperature 0 the second answer would be the same.
- `LLM_BACKEND=stub` replaces the model: extraction answers come from truth files, choices take the first
  candidate. Stub runs are recorded with backend `stub` and are left out of the fine-tuning export.

To find the call sites again:

```
grep -rn -E "\.ask\(|\bchoose\(" src tools --include=*.py
```

## Register

One row per LLM request type. "Calls" are for the 25 invoices in the dev database whose latest reading is
by the real model, as reported by `python -m aap.eval.model_use`; the stage 5 counts are from `extraction_run`.

| # | Stage | Node / task | What the model is asked | Reached when | Max tokens | Recorded in | Calls (dev DB) | Fine-tune? |
|---|---|---|---|---|---|---|---|---|
| 1 | 5 Extract | `extract` | Read the whole invoice: header, tax rows, lines | Every document | 4,096 | `extraction_run.raw_output` | Every invoice | **Yes, LoRA, once real labels exist** |
| 2 | 5 Extract | `extract` (lines only) | Read the lines of pages 5 and onward | More than 4 page images | 4,096 | `extraction_run.raw_output` | 0 (`multi_request`) | Same adapter as row 1 |
| 3 | 5 Extract | `extract_taxes` | Read only the tax rows again | Tax rows do not add up to the total | 1,024 | `extraction_run.raw_output` (chunk `taxes`) | 3 of 66 v5 runs, fixed 2 | Same adapter as row 1 |
| 4 | 7 Map | `map.supplier` | Which supplier sent this invoice? | No value map, tax ID, exact name or email domain; no clear fuzzy winner | 300 | `agent_decision` | 16 of 25 invoices, all "none" | No |
| 5 | 7 Map | `map.site` | Which supplier site matches the address? | Several pay sites, no clear address winner | 300 | `agent_decision` | 0 | No |
| 6 | 7 Map | `map.terms` | Which payment terms mean the same? | No value map, exact name, parsable pattern or printed due date | 300 | `agent_decision` | 0 | No |
| 7 | 7 Map | `map.category` | Which purchasing category fits this line? | Non-PO line, no past line of the supplier with the same text | 300 | `agent_decision` | 0 of 43 lines | No |
| 8 | Agents | `agent.pair_lines` | Which PO line is this invoice line? | Best score below 0.80, or less than 0.15 ahead of the next | 300 | `agent_decision` | 1 of 98 lines | Maybe, measure first |
| 9 | Agents | `agent.gl_code` | Which GL account for this non-PO line? | No identical history; similar lines disagree or similarity below 0.90 | 300 | `agent_decision` | 0 of 43 lines | No (tune the embeddings instead) |

Every other stage is code. Details per stage follow.

## Stage by stage

### Stages 1-4: intake, unpack, parse, route — no LLM

File handling, page rendering, PDF text layer and the per-page route decision (`TEXT_QUALITY_THRESHOLD`).
The route decides what the model sees in stage 5: image and text layer, image only, or text only.

### Stage 5: extract — LLM (rows 1-3)

`stage_extract()` in `src/aap/pipeline/stages.py` calls `extract()`. Up to three kinds of request:

1. **Main request.** System prompt `extract_system.txt`. Content: each page image plus its text layer when
   the route allows it, or a note that the page has no usable text layer. Schema: `ExtractedInvoice`.
   When a page in the request is image-only, `extract_grounded.txt` is appended and the schema becomes
   `ExtractedInvoiceGrounded`, which adds a bounding box per header field.
2. **Lines-only requests.** Up to `LLM_MAX_IMAGES_PER_REQUEST` (4) images go in one request. Longer
   invoices are split; requests after the first append `extract_lines_only.txt`, only their lines are kept,
   and the invoice is flagged `multi_request`.
3. **Tax re-read (prompt v5).** When subtotal + taxes + freight differs from the total by more than
   `AMOUNT_TOLERANCE` (`tax_gap()`), one more request with `extract_taxes.txt` reads only the tax rows,
   on the pages of the last request. Schema: `ExtractedTaxes`. The new rows are used only when they close
   the gap; the invoice is then flagged `tax_reread`. A freight amount printed on a tax row is ignored for
   this check (`freight_on_tax_row()`).

After the model: `source_check()` compares each value with the text layer (rule C12) and flags values not
found on the page. This is code, not a second model call.

Prompts live in `src/aap/extraction/prompts/<version>/`; `CURRENT_VERSION` is in `prompts/__init__.py`
and is stored on every `extraction_run` as `prompt_version`.

### Stage 6: normalize — no LLM

Dates, amounts, currency, price basis, credit memo detection. An ambiguous value is flagged for the clerk,
not guessed.

### Stage 7: map — LLM as last resort (rows 4-7)

`map_invoice()` in `src/aap/mapping/map.py`. Each field is tried in order (value map, exact match, rules,
fuzzy match) and only an unclear fuzzy result reaches `choose()` with up to 5 candidates. A model pick is
flagged `check_map`; "none" gives `no_map`. An approved model pick for supplier or terms becomes a
value-map proposal on the admin page, so the same printed value is resolved without the model next time.

Unit of measure, tax code, org, location and cost center are mapped without a model.

**Why `map.supplier` is asked so often now:** the customer samples come from suppliers that are not in the
mock supplier master (Énergir, Metro Corral, N Wasserstrom, Level Access, Mspark). The fuzzy step finds only
weak candidates and the model correctly answers "none" each time. The fix is supplier master data, not the
model.

### Stage 8: validate — no LLM

Rules C1-C12 and the PO and reference checks. A blocked invoice must have a rule as its reason.

### Agents: triage, find PO, pair lines, three-way match, hold, GL coding, finalize — LLM in two nodes (rows 8-9)

- `triage`, `find_po`, three-way match, `hold`, `finalize`: code.
- `pair_lines` (`src/aap/agents/nodes/matching.py`): each invoice line is scored against each PO line and
  assigned; an ambiguous line goes to `choose()` with up to 5 PO lines scoring at least 0.3. A pair from the
  model adds the reason `LINE_PAIRED_BY_MODEL`.
- `gl_code` (`src/aap/agents/nodes/gl.py`): non-PO lines only. Identical history first, then the 5 most
  similar past lines (bge-m3); only when those disagree or are below `GL_SIM_AUTO` (0.90) does the model
  choose among their accounts. The model may answer "review", and its choice always goes to review.

### Approval, stages 10-11: submit and result — no LLM

Interface rows, the Oracle import and the result poll. At approval, the confirmed non-PO lines are added to
the GL history and embedded with bge-m3 (`src/aap/submit/decision.py`); that is the embedding model, not
the LLM.

### Paths that call the LLM again

| Trigger | Re-runs from | LLM calls repeated |
|---|---|---|
| Clerk corrects the reading on the invoice page | Stage 6 (normalize) | Rows 4-9; the extraction is not re-run, the clerk's values replace it |
| Received quantities change for an invoice on hold (`recheck_holds`) | Agents (triage) | Rows 8-9 |
| Stage retry after an error (up to 4 attempts) | The failed stage | That stage's rows |
| Stuck-document recovery job | The next stage | That stage's rows |

## Outside the pipeline

| Where | What | LLM? |
|---|---|---|
| `GET /health/llm` (`src/aap/api/main.py`) | Lists the vLLM models to check the server is up | Server ping, no generation |
| `python -m tools.vllm_smoke` | Runs `extract()` on one PDF | Yes, row 1 |
| `python -m aap.eval` (`src/aap/eval/harness.py`) | Runs stages 3-8 (and the agents with `--agents`) on an eval set | Yes, rows 1-9 |
| `python -m aap.eval.model_use` | Counts the `agent_decision` rows per node | No, reads the database |
| `python -m aap.finetune.export` | Writes `extraction.jsonl` and `choices.jsonl` for training | No, reads the database |
| bge-m3 (`src/aap/embed/`), backfill job every 5 minutes | Line embeddings for GL similarity | No, embedding model on the CPU |

**Removed:** writing vendor emails and reading their replies. The mailbox was taken out of scope on
2026-10-03 (`migrations/versions/0003_remove_mailbox.py`) and `agents/graph.py` has no vendor email node.
`HOW-IT-WORKS.md` section 1 still lists these as LLM uses.

## Fine-tuning scope apart from extraction

Short answer: extraction is the only step worth fine-tuning today. The other LLM uses are either rarely
reached, or fail for reasons a tuned model cannot fix.

| Use | Verdict | Why |
|---|---|---|
| Tax re-read (row 3) | Part of extraction | Same model and same skill. Training targets already include the `taxes` rows, so a better extraction adapter makes the re-read rarer. No separate adapter. |
| `agent.pair_lines` (row 8) | Only real candidate; measure first | Semantic matching of product descriptions, candidates are in the prompt, nothing Oracle-specific to memorise. But it was reached for 1 of 98 PO lines on the samples, and its accuracy is not measured. Cheaper options first: tune `MATCH_AUTO_ACCEPT` / `MATCH_MARGIN`, add embedding similarity to the score. |
| `map.supplier` (row 4) | No | The answer is an Oracle supplier ID, which changes without notice. The current calls are caused by missing supplier master data. |
| `map.site`, `map.terms`, `map.category` (rows 5-7) | No | Not reached on the samples. Repeated values belong in value maps, which take effect at once. |
| `agent.gl_code` (row 9) | No; tune bge-m3 instead, later | The account depends on the supplier's own history, which grows with every approval. Contrastive tuning of the embeddings could raise the share of lines passing 0.90, once the GL history is real and not synthetic. |
| Choice model in general | Model choice, not tuning | A separate text model (Qwen3-8B, compose profile `gpu2`) is defined, but nothing routes `choose` requests to it: there is one `VLLM_BASE_URL`. A tuned extraction model would also answer the choices, so check rows 4-9 in the eval before swapping it in. |

**Training data so far:** no clerk corrections yet (0 `extraction_run` rows with backend `user`). Every
choice is stored in `agent_decision` with its prompt and candidates, and `aap.finetune.export` pairs it
with the posted value once a clerk approves the invoice.

## Checklist when the LLM use changes

- New `llm.ask()` or `choose()` call: add a row to the register with its node name, trigger and token limit.
- New prompt version: update "Last checked" and the prompt notes under stage 5.
- New `choose()` node: give it a unique `node` name, so `aap.eval.model_use` counts it and the export labels it.
- Refresh the counts with `python -m aap.eval.model_use` (in the `api` container).
