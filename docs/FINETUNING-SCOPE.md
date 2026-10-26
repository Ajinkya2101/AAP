# Fine-tuning scope

Where a fine-tuned model could replace or improve a step of this pipeline, where it could not, and why.
Figures are from the dev database on 2026-10-11: 631 invoices extracted by the real model
(Qwen2.5-VL-7B) and the 515-invoice held-out eval. All invoices are synthetic (`tools/invoicegen`).

For which model runs where today, see [MODEL-USAGE.md](MODEL-USAGE.md).

## Verdict per step

| Step | Today | Fine-tune? | Main reason |
|---|---|---|---|
| Parse and route (stages 3-4) | Code | No | Deterministic file handling; nothing to learn |
| **Extraction (stage 5)** | Vision model | **Yes, LoRA, once real labels exist** | The only step where the model does all the work and errors are measurable |
| Normalize (stage 6) | Code | No | Dates and amounts must be exact and explainable |
| Supplier and site mapping (stage 7) | Rules, then model | No | The model was asked 0 times in 631 invoices |
| Payment terms mapping (stage 7) | Value map, rules, then model | No | 8 calls, all caused by bad extraction input |
| Unit of measure mapping (stage 7) | Value map, exact match | No | Never reaches a model |
| Category mapping (stage 7) | History, then model | No | 2.4% of non-PO lines; history and value maps are cheaper |
| Validation (stage 8) | Code | No | Control checks must be deterministic and auditable |
| PO line pairing (agents) | Scoring, then model | Maybe, after measuring | Reached often enough to matter, but quality is unmeasured |
| Three-way match, holds (agents) | Code | No | Tolerances are Oracle policy, not a judgement |
| GL coding (agents) | History lookup, embeddings, then model | Not the LLM; embedding tuning is optional | The model is asked for about 0.1% of lines |
| Submit to Oracle (stages 10-11) | Code | No | Interface writes; no model involved |

## How feasibility was judged

A step is a fine-tuning candidate only when all four of these hold:

1. **A model already does the work there, or should.** A step that is correct by construction in code
   (arithmetic, tolerance checks, interface rows) gains nothing from a model and loses auditability.
2. **The step is reached often enough.** A model asked on 1 invoice in 100 cannot move the overall result
   much, however well it is tuned.
3. **The knowledge is stable.** Anything that lives in Oracle (supplier IDs, terms IDs, GL accounts) changes
   without notice. A model that memorised it is wrong the day after the change, and retraining is slow.
   Such knowledge belongs in the prompt or a lookup table, where it is replaced by the next reference sync.
4. **Correct answers can be collected.** Fine-tuning needs examples of the right output. Until 2026-10-11
   the application stored none.

## Step by step

### Parse and route (stages 3-4): not feasible

These stages open the file, render pages, read the PDF text layer and decide per page whether the text layer
is good enough (`TEXT_QUALITY_THRESHOLD`). The decision is a score against a threshold. There is no
ambiguity a model would resolve better, and a wrong route is cheap: the page goes to the vision model as an
image either way.

### Extraction (stage 5): feasible with LoRA

This is the one real candidate. The vision model reads the page images and text layer and returns every
header field and line. Nothing else in the pipeline can recover a value it misreads.

**Where it stands (held-out eval, prompt v2):**

| Tier | Invoices | Header accuracy | Line F1 | Median time |
|---|---|---|---|---|
| Digital PDF | 464 | 99.8% | 98.5% | 10.9 s |
| Scanned | 51 | 96.9% | 100% | 23.5 s |

**What a tuned model could fix:**

- **Character confusion on scans:** `AII28916` read as `ALL28916`, `AII66009` as `AI66009`. This is a
  visual recognition error. Prompt wording cannot fix it; examples of the same fonts and scan noise can.
- **Value in the wrong field:** a label returned as the value ("Terms"), or a total repeated as a subtotal.
  Prompt v3 addresses the patterns seen so far. Layout-specific cases that remain are a tuning target.
- **Speed and size:** a tuned smaller model (for example a 3B vision model) might match the 7B model on this
  narrow task, which would roughly halve GPU memory and time per invoice. This is untested.

**Why LoRA and not full fine-tuning:**

- The task is narrow (one document type, one output schema). Low-rank adapters on the language layers are
  normally enough for that; the vision encoder can stay frozen at first.
- Full fine-tuning of a 7B model needs the weights, the gradients and the optimizer state in memory at once,
  several times the 16.6 GB of the weights alone (my estimate: 60 GB or more). The dev GPU has 24 GB.
- With hundreds to a few thousand examples, full fine-tuning overfits sooner than LoRA and risks losing
  what the base model already does well (other languages, unseen layouts).

**Technical conditions for a useful adapter:**

- **Train in the serving format.** Same system prompt (`prompts/v4` today), same image resolution
  (`VLLM_MAX_PIXELS`), same text-layer attachment, same JSON key order as `ExtractedInvoice`. A mismatch
  between training and serving input costs more accuracy than the tuning gains.
- **Keep schema-constrained decoding.** vLLM enforces the JSON schema today. A tuned model keeps that, so
  tuning cannot break output validity; it only changes which values are written.
- **Image-only pages need care.** For scanned pages the model also returns a bounding box per header field.
  Clerk corrections carry no boxes, so either train only on the values or keep the model's own boxes.
- **Evaluate on invoices the generator did not produce.** See "What blocks it today".

**What blocks it today:**

- **No real data.** All 573 test invoices come from one generator with 11 layouts. An adapter trained on
  them would score near 100% on the eval, because the eval comes from the same generator, and that score
  would say nothing about real supplier invoices.
- **No human labels yet.** Label capture was added on 2026-10-11 (see "What is in place"); the tables are
  still empty.

### Normalize (stage 6): not feasible

Normalization turns "1.234,56 €" into `1234.56` and "03/04/2026" into a date, using the supplier's country
as a hint. These are parsing rules with a right answer. When the code cannot decide (two possible date
formats) it raises a flag for the clerk. A model would answer confidently in exactly those cases, which is
the opposite of what a payment system needs. Failures here (37 date and 34 amount parse flags on 631
invoices) are fixed by adding a format to the parser, which is permanent and testable.

### Supplier and site mapping (stage 7): not feasible

The supplier is found by value map, tax ID, exact name, email domain, then fuzzy name match. Only an
unclear fuzzy result goes to the model. On 631 invoices that happened 0 times, for supplier and for site.

Even if it happened more often, the answer is an Oracle supplier ID. Suppliers are added and deactivated in
Oracle; a model cannot know that. The candidates must come from the reference copy in the prompt, which is
what the code does now. When a clerk approves a model pick, a value-map proposal is written, and after
approval the same printed name is resolved by lookup.

### Payment terms mapping (stage 7): not feasible

The order is: value map, exact name, regex parsing (immediate, "2/10 net 30", end of month, "net N"), then
the model. The model was reached 8 times in 631 invoices. All 8 inputs were extraction mistakes such as
"Terms", "Subtotal" and "Account: 1714-5295", and the model correctly answered "none" each time.

So there is no terms-matching problem to tune. The cause was upstream and is addressed in prompt v3. Real
phrases that the rules miss should become value-map rows, which take effect at once and can be removed.

The customer's real samples (2026-10-23) often print only a due date. Since 2026-10-26 the terms are then
derived from the days between invoice and due date, also when the model's printed terms are not found on
the page (PLAN-REAL-INVOICES.md R1.3), which takes more cases away from the model.

### Unit of measure mapping (stage 7): not feasible

Value map, then exact match on the Oracle unit or its code, otherwise a "no map" flag. No model is involved.
An unknown unit is one new value-map row.

### Category mapping (stage 7): not worth it

For non-PO lines the category comes from the supplier's past lines with the same text; otherwise the model
picks from the top 5 fuzzy candidates. That happened for 35 of 1,436 non-PO lines (2.4%), and the model
answered "none" 8 times.

A tuned model is not the right tool here:

- The category list is Oracle data and changes.
- Most misses are a retrieval problem (the right category is not among the 5 fuzzy candidates), which
  tuning the picker does not fix. Better candidates would come from embedding search over category
  descriptions.
- Each confirmed line becomes history, so the same text is resolved without the model next time.

### Validation (stage 8): not feasible

Twelve rules: required fields, arithmetic, tax ID format, supplier status, period open, duplicates, bank
change, PO status, FX, reference checks. Each has a defined pass or fail. An auditor must be able to see why
an invoice was blocked; "the model thought so" is not an acceptable reason. These stay code.

### PO line pairing (agents): possible, measure first

Each invoice line is scored against each PO line (description, quantity, price) and assigned. A pair is
accepted without the model when the score is at least 0.80 and leads the next candidate by 0.15. Otherwise
the model picks among up to 5 candidates.

- **Volume:** in the stub runs the model was needed on 37 of 238 PO invoices (56 lines). This is the only
  choice point reached on a meaningful share of invoices.
- **Why the verdict is open:** those 56 answers came from the stub, which always takes the first candidate.
  How well the real model does here is not yet measured. Run `python -m aap.eval --agents` and
  `python -m aap.eval.model_use` first.
- **Why tuning could work:** matching "Dell Latitude 5440 14in i7" to "Laptop, 14 inch, business" is
  semantic similarity, the candidates are in the prompt, and nothing Oracle-specific has to be memorised.
  The knowledge is stable.
- **Options, cheapest first:** tune the two thresholds on real data; add embedding similarity to the score;
  a LoRA on the choice model; a small cross-encoder trained on (invoice line, PO line) pairs.
- **Labels:** when a clerk changes a PO line on the review screen, the approved version records it. The
  export writes these as labelled choices.

### Three-way match and holds (agents): not feasible

Quantity and price variances are compared with the tolerance template of the supplier site and turned into
Oracle hold codes. The tolerances are finance policy stored in Oracle. This is arithmetic.

### GL coding (agents): not for the LLM

For non-PO lines the account comes from, in order: identical past lines of the supplier; the 5 most similar
past lines (bge-m3 embeddings) when they agree and similarity is at least 0.90; otherwise the model chooses
among the accounts found, and its choice always goes to review.

- **Volume:** the model was asked for 7 of 5,671 lines in the stub runs.
- **Why retrieval beats a tuned model here:** the right account depends on this supplier's own history and
  on the chart of accounts, both of which change. A confirmed line is added to the history with its
  embedding immediately, so the next identical line is coded without a model. A tuned model would need
  retraining to learn the same thing.
- **What could be tuned instead:** the embedding model. Contrastive training on the 16,754 history lines
  (lines with the same account close together, others apart) could raise the share of lines that pass the
  0.90 threshold. This is optional, and the present history is synthetic, so it should wait for real data.
- **Review volume is not a model issue.** `GL_REVIEW` is the most common review reason in the dev database,
  but nearly all of those lines were coded from identical history with a small share of conflicting past
  lines. They appear to predate the current rule that accepts 90% agreement; re-running the agents on them
  should clear most.

### Submit to Oracle (stages 10-11): not feasible

Writing the open-interface rows, running the import and reading the result. No judgement is involved.

## Methods compared

| Method | Fits | Does not fit |
|---|---|---|
| Prompt change | Wrong-field errors, missed labels. Free, immediate, versioned (`prompts/vN`, today v4) | Visual misreads |
| Value map or history row | Any repeated printed value: terms, units, supplier names, GL text | First occurrence of a new value |
| LoRA / QLoRA on the vision model | Extraction, once 500 to 1,000 corrected real invoices exist (my estimate) | Anything needing Oracle IDs from memory |
| LoRA on a text choice model | PO line pairing, if measurement shows a need | Choice points reached under 3% of the time |
| Embedding fine-tune (bge-m3) | GL similarity, with real history | Small or synthetic history |
| Small classifier or cross-encoder | PO line pairing, category | Extraction |
| Full fine-tuning | Nothing here | 24 GB GPU, small data, narrow task |

## Hardware and serving

- **Training:** the dev GPU has 24 GB and vLLM holds about 22 GB while serving. Training means stopping
  vLLM. QLoRA (4-bit base, batch size 1 with gradient accumulation) on single-page invoices should fit;
  this is an estimate and has not been run.
- **Serving:** merge the adapter into the weights and serve the merged model exactly as today. Whether vLLM
  can load a separate LoRA next to `fp8` quantization has not been tested; merging avoids the question.
- **One model, two jobs:** `CHOICE_MODEL` defaults to the extraction model, so a tuned extraction model
  would also answer the choice calls. Check the choice calls in the eval before switching.

## What is in place (2026-10-11)

- **Corrections of the reading:** the invoice page and `POST /api/documents/{id}/corrections` record the
  clerk's values next to the model's reading. These pairs are the extraction labels.
- **Value-map proposals:** an approved model pick for supplier or terms becomes a proposal on the admin page.
- **Usage report:** `python -m aap.eval.model_use` shows how often each choice point asks the model.
- **Export:** `python -m aap.finetune.export --out <dir>` writes `extraction.jsonl`, `choices.jsonl` and
  the page images. Extraction targets are in the model's flat answer shape, including the `taxes` rows of
  the v4 schema.

## Order of work

1. Process real invoices and have clerks correct them. Nothing else produces training data.
2. Run the agents with the real model on a labelled set and read the usage report.
3. Fix what prompts, value maps and thresholds can fix. Re-measure.
4. When about 500 to 1,000 corrected real invoices exist, hold out a share that is never trained on, train
   a QLoRA adapter for extraction, and accept it only if it beats the base model on that held-out share.
5. Consider PO line pairing only if step 2 shows the real model is both asked often and often wrong.
