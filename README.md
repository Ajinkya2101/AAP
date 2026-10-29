# AAP Module

Autonomous AP: invoice pipeline, agent layer and a mock Oracle EBS, all local.
Design: [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) (all phases implemented; progress in
[docs/IMPLEMENTATION-PLAN-STATUS.md](docs/IMPLEMENTATION-PLAN-STATUS.md)).
How the code works, end to end with diagrams: [docs/HOW-IT-WORKS.md](docs/HOW-IT-WORKS.md).
Current work, making it ready for the customer's real invoices: [docs/PLAN-REAL-INVOICES.md](docs/PLAN-REAL-INVOICES.md).

## Run

```bat
start_container.bat --cpu        :: stub LLM, no GPU
start_container.bat              :: GPU mode (vLLM + Qwen2.5-VL-7B; first start downloads ~17 GB)
start_container.bat --seed       :: re-seed the mock EBS and refresh the reference copy
start_container.bat stop | status | logs [service]
```

| URL | What |
|---|---|
| http://localhost:8080/docs | Swagger: upload (`POST /api/documents`), documents, admin ref sync |
| http://localhost:8080/ui | Dashboard: figures, upload, records |
| localhost:5432 | PostgreSQL (`aap`, `ebs`); roles in `docker/postgres/init` |

`migrate` (one-shot) creates the mock EBS, seeds it (Faker, `SEED=42`), runs Alembic,
installs the Procrastinate schema, loads default value maps and runs the first reference sync.

## Try the pipeline

```bat
:: 15 golden scenarios + 40 random invoices, then deliver them through the API
docker compose exec worker python -m tools.invoicegen --count 40 --out /data/filestore/invoicegen
docker compose exec api python -m tools.invoicegen.deliver --dir /data/filestore/invoicegen --api http://localhost:8080
```

Each document goes RECEIVED -> PARSED -> ROUTED -> EXTRACTED -> NORMALIZED -> MAPPED -> VALIDATED,
one Procrastinate job per stage. `GET /api/documents/{id}` shows the canonical invoice with
per-field provenance and the validation results.

Simulate EBS events: `docker compose exec worker aap-mock receive --po 4500123 --line 1 --qty 2`
(also `close-po`, `cancel-line`, `disable-ccid`, `end-date-site`, `import-status`, `import-run`).

vLLM smoke test (Phase 0): `docker compose exec worker python -m tools.vllm_smoke`
(vLLM's debug port on the host is `VLLM_HOST_PORT`, default 8100).

### Review in the browser

`http://localhost:8080/ui`: the dashboard (figures over every processed invoice, an upload queue that takes
many files, by picker or drag and drop, and follows each one through the pipeline, the
records with search, a status filter, paging, and Delete per row or for the ticked rows), invoice detail (original file next to the extracted fields,
validation, agent decisions, holds, Oracle status; approve / reject with per-line PO line or GL account
corrections; delete) and an admin page (job queue, failed documents, reference sync, eval runs).
No login in the dev build; "acting as" names you in the audit.

Invoices come in by upload only (the dashboard or `POST /api/documents`); there is no mailbox and the
application sends no email. A saved email (`.eml`, `.msg`) can be uploaded like any other file: its
attachments become the invoices. When the supplier has to fix something (invoice maths, missing PO
number) the invoice goes to review with that as the suggested action.

Delete (`DELETE /api/documents/{id}` or the Delete buttons) removes the document, its extracted data,
history and files, so the same file can be uploaded again. The audit trail keeps a `document.deleted`
event, and an invoice already posted to Oracle stays in Oracle.

### Eval (Phase 6)

```bat
:: stub (checks the harness) or real model: LLM_BACKEND=vllm, vLLM running
docker compose run --rm -e LLM_BACKEND=vllm -v "%CD%	est-data:/app/test-data:ro" worker ^
  python -m aap.eval --dir test-data/invoices-dev-55 --name my-run
docker compose run --rm worker python -m aap.eval --rescore <run id>   :: new metrics, no model calls
```

Reports header / line-item accuracy per tier (digital, scanned) and per field against the
PLAN 4 gates; results are stored in `eval_run` / `eval_result` and listed on the admin page.

### Audit check

`docker compose exec worker python -m aap.audit_check [--since 2026-09-29]` verifies that every
status change, agent decision, approval and Oracle submission is traceable.

### Approve and post to (mock) Oracle

`POST /api/documents/{id}/decision` with `{"action": "approve", "by": "me"}` freezes an `approved`
version and submits it through the Payables Open Interface; the document goes APPROVED -> SUBMITTED ->
CREATED (or ORACLE_ERROR with the reject codes). Optional `corrections` set a PO, PO line or GL account per
line: `{"lines": {"1": {"po_number": "4500210-2", "po_line_number": 1}, "2": {"dist_account": "01-510-6420-0000"}}}`
(`4500210-2` = release 2 of blanket agreement 4500210; without `po_number` a line keeps its own PO or the
header PO). An invoice can be on several POs: each line is paired only with lines of the PO printed on it.
`GET /api/documents/{id}/submissions` shows the Oracle request, invoice id and holds.

### Download the extracted and validated data

- **One invoice, CSV:** "invoice CSV" on the invoice page, or `GET /api/documents/{id}/invoice.csv`. One
  standard layout for every invoice: one row per line, with the document, header (every field),
  validation (result, failed and warning checks, messages), review reasons, errors, flags and notes
  repeated on each row; an invoice without lines is one row. Dates `YYYY-MM-DD`, plain decimal amounts,
  lists separated by `; `. The columns never vary, so files can be appended. "Payment terms" and
  "Due date" are what Oracle gets (one derived from the other when only one is printed); "Payment terms
  on invoice" and "Due date on invoice" hold only what the invoice prints.
- **One invoice, everything:** "Download everything (ZIP)", or `GET /api/documents/{id}/export.zip`: that
  `invoice.csv` plus the stored JSON (`invoice.json`, `validation.json` every run, `extraction_runs.json`
  every run, `model_output/` exactly what the model returned per request).
- **Several invoices:** "Download selected" or "Download all / this filter" under Records, or
  `GET /api/export.zip?status=IN_REVIEW&q=...` (or `?ids=<id>&ids=<id>`). The same per invoice under
  `documents/`, plus `invoices.csv`: all their rows under one header.
- **Invoice JSON only:** `GET /api/documents/{id}/invoice.json` (newest version, the approved one after approval).

### APLOAD file (the customer's AP interface format)

Their existing AP interfaces load a pipe-delimited file of H (header) and L (line) rows
(sample `APILOAD_20251118.csv`; format in `src/aap/submit/apload.py`). The same rows for our invoices:

```bat
:: one invoice (before approval: the version a clerk would approve now, header X-AAP-Approved: false)
curl http://localhost:8080/api/documents/<id>/apload
:: every approved invoice, or the ones given with --document
docker compose exec worker python -m aap.submit.apload --out /data/filestore/apload/APILOAD_20261026.csv
```

Approval still posts through the open interface; whether the APLOAD file replaces that is open
(docs/PLAN-REAL-INVOICES.md, R2.4). A blanket release goes into `ReleaseNumber`. APLOAD has one
`PONumber` per invoice, so an invoice whose lines are on several POs is refused there (R2.8).

### Store / location -> cost center

Non-PO lines take the natural account from history and the cost center from the invoice's store or
location, once the functional team's table is loaded (`GL_COST_CENTER_SEGMENT`, `GL_SEGMENT_SEPARATOR`):

```bat
:: header row with a location column (location / store) and a cost-center column (cost_center / cc / segment3)
docker compose cp stores.csv worker:/tmp/stores.csv
docker compose exec worker python -m aap.mapping.locations /tmp/stores.csv --dry-run
docker compose exec worker python -m aap.mapping.locations /tmp/stores.csv
```

### Corrections and training data

Before any model fine-tuning the application collects labels from the clerks' work:

- **Correct the reading.** The invoice page has a "Correct what was read from the invoice" form
  (`POST /api/documents/{id}/corrections` with `{"header": {"invoice_number": "AII28916"}}`, optional `lines`,
  `remove_lines`, `add_lines`). The corrected values run through stages 6-8 and the agents again; the model's
  reading and the correction are both kept (`extraction_run`, backend `user`).
- **Value-map proposals.** A supplier or payment terms the model chose becomes a proposal once a clerk approves
  the invoice. Approve it on the admin page (`POST /api/value-map-proposals/{id}/decision`) and the same printed
  value is mapped without the model from then on.
- **How often is the model asked?** `docker compose exec worker python -m aap.eval.model_use` reports the model
  calls per decision point on real-model invoices. `python -m aap.eval --dir <set> --agents` also runs the agents,
  then `python -m aap.eval.model_use --eval-run <run id>` covers PO matching and GL coding too.
- **Export.** `docker compose exec worker python -m aap.finetune.export --out /data/filestore/finetune` writes
  `extraction.jsonl` (pages, model output, corrected target), `choices.jsonl` and the page images.

## Layout

| Path | Contents |
|---|---|
| `src/aap/ebs_mock/` | EBS DDL (`ddl/`), seeder, golden scenarios S1-S15, APXIIMPT import program, concurrent manager, `aap-mock` CLI |
| `src/aap/adapters/` | `ErpReadPort` / `ErpWritePort`; `MockEbsAdapter` (SQL against the `apps` views) |
| `src/aap/db/` | SQLAlchemy models, Alembic migrations, `migrate` entry point |
| `src/aap/ingest/` | Stages 1-4: intake, unpack (email), parse (PDF/image/sheet/text), route |
| `src/aap/extraction/` | Stage 5: versioned prompts (current `v4`), extractor (4-image chunks), source check |
| `src/aap/normalize/`, `mapping/`, `validation/` | Stages 6-8 |
| `src/aap/llm/` | `LLM_BACKEND=vllm` or `stub` |
| `src/aap/agents/` | LangGraph agent layer: triage, PO match, GL coding, `recheck_holds` |
| `src/aap/submit/` | Stages 10-11: approve / reject, corrections of the reading, open-interface submit, result polling; `apload.py`: the customer's APLOAD file |
| `src/aap/embed/` | bge-m3 embeddings (ONNX, CPU) for GL-history similarity; backfill job |
| `src/aap/eval/` | Eval harness, metrics, report (Phase 6); `model_use`: how often the model is asked |
| `src/aap/finetune/` | Export of the training examples collected from corrections and approvals |
| `src/aap/api/ui.py` | Minimal UI (Jinja2 + HTMX): dashboard, invoice detail, admin |
| `src/aap/pipeline/delete.py` | Delete a document with everything derived from it |
| `src/aap/worker/` | Procrastinate app, stage tasks, periodic jobs |
| `tools/invoicegen/` | Synthetic invoices: 11 layouts, scan noise, emails, truth sidecars |

## Tests

```bat
uv sync
uv run pytest tests/unit                  :: no database
uv run pytest tests/integration           :: starts a pgvector container (testcontainers)
```

The e2e test (`tests/e2e`, S1-S15 through the whole pipeline) needs WeasyPrint's native
libraries, so run it in the app image against a throwaway database:

```bat
docker run -d --name aap-testpg --network aap_default -e POSTGRES_PASSWORD=postgres ^
  -v "%CD%\docker\postgres\init:/docker-entrypoint-initdb.d:ro" pgvector/pgvector:pg16
docker compose run --rm --no-deps -e PG_TEST_HOST=aap-testpg api python -m pytest tests/e2e -s
```

To run the scenarios against the real model, use the `worker` service (it is on the vLLM network) with
`-e TEST_LLM_BACKEND=vllm` (about 12 minutes).

## Notes

- The seed is anchored at `SEED_ANCHOR_DATE` (default 2026-09-28): AP periods AUG-26 and SEP-26 are
  open. Re-seed with a later anchor when the calendar moves on.
- `REJECT_LOOKUP_CODE` is `VARCHAR2(30)` as in EBS, so the line-total reject code is
  `LINES AMOUNT MISMATCH` (the plan's longer wording does not fit).
- `MTL_SYSTEM_ITEMS_B.ATTRIBUTE1` holds the vendor's catalogue wording (mock-only DFF) so generated
  invoices describe items differently from the PO.
- `GL_DATE_POLICY` picks the GL date: `invoice_date` (default; the invoice date if its AP period is open,
  else today) or `today` (the EBS Invoice Workbench default).
- The worker puts jobs a crashed worker left running back on the queue at start
  (`WORKER_RECOVER_ON_START=true`). Set it to `false` before running more than one worker process.
