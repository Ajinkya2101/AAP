# AAP Module: Real-Invoice Readiness Plan

| Item | Value |
|---|---|
| Status | Active |
| Started | 2026-10-26 |
| Input | EBS walkthrough with the functional team (2026-10-22); Dhivya's samples: 5 invoice PDFs and `APILOAD_20251118.csv` |
| Builds on | IMPLEMENTATION-PLAN.md (all phases done), FINETUNING-SCOPE.md |

Legend: ✅ done and verified · 🟠 in progress · ⬜ not started · ⏸ waiting on input

## 1. What the samples showed

The 5 PDFs ran through the real model (Qwen2.5-VL-7B, prompt v3) on 2026-10-23. All 5 reached review. Invoice
number, date and total were right on every one, but:

| Finding | Invoice(s) | Item |
|---|---|---|
| Worker crashed (PDFium is not thread-safe; parse runs 8 jobs as threads) | 3 uploads at once | R1.1 |
| Jobs running at the crash stay `doing` forever; `requeue_stalled` skips them | same | R1.2 |
| Only a due date is printed (no terms); the model invented "Net 30" or put a label into the terms | Level Access (60 days), Mspark (17 days) | R1.3 |
| Two taxes (GST + QST); only one is read, so the total check fails | Énergir | R1.5, R1.6 |
| Prices per thousand (`$56.21 /M`) or in cents (`15,99491 ¢/m³`) make the line maths fail | Mspark, Énergir | R1.6 |
| Invented values on image-only pages: due date, bank details `000`, terms | Corral, Wasserstrom, Level Access | R1.5, R1.6 |
| PO number read from "Mark For"; the invoice's line numbers read as PO lines | Wasserstrom | R1.5 |
| Freight and handling read as an item line | Wasserstrom | R1.6 |
| Québec invoice read as USD (only "$" printed) | Énergir | R1.6 |
| "Please use new routing number effective immediately": the model took the old number, nothing flagged it | Level Access | R1.5, R1.8 |
| Tax rate invented, then the tax-maths check fails | Mspark | R1.8 |
| Store / location printed ("STORE 316", "KRS 51", "#0368"); the CSV shows GL segment 3 is the location | all, CSV | R1.5, R1.7, R2.1 |
| Credit and debit memos are not recognized (type is STANDARD unless the total is negative) | call | R1.5, R1.6, R1.8, R1.9 |

The CSV (`APILOAD`) is the format of their existing AP interfaces: pipe-delimited H and L records, dates
`MMDDYYYY`, header amounts with 5 decimals, tax as a header amount (not a line), supplier by number and site
name, an 8-segment account with `.` separators. → R1.10.

## 2. Phase R1: fixes that need no outside input

| # | Item | Where | Status |
|---|---|---|---|
| R1.1 | Lock around every PDFium call | `ingest/parse/pdf.py` | ✅ (5 PDFs parsed in parallel, no crash) |
| R1.2 | On worker start, put jobs left `doing` by a crashed worker back to `todo` (one worker container, so every `doing` job at start is orphaned); setting `WORKER_RECOVER_ON_START` | `worker/__main__.py`, `config.py` | ✅ |
| R1.3 | Payment terms from the due date: printed terms that parse win; otherwise (nothing printed, unparseable text, or terms not found on the page) the days between invoice and due date pick a single-installment term; no such term → supplier default + `check_map`. Printed terms that disagree with the due date get `terms_due_date_conflict` | `mapping/map.py` | ✅ |
| R1.4 | GL date policy setting `GL_DATE_POLICY=invoice_date\|today` (default: today's behaviour, invoice date if its period is open) | `mapping/map.py`, `config.py` | ✅ |
| R1.5 | Extraction schema + prompt v4: `document_type`, `location_ref`, `bank_change_notice`, `taxes` (one entry per printed tax row); rules: due date / bank details / terms only when printed, PO labels ("Your order number"; "Mark for", job and sales-order numbers are not POs), the supplier is the letterhead / remit-to company, `po_line_ref` only for a printed PO line reference, freight rows. Stub and old truth files keep working (missing keys → null) | `schemas/source.py`, `extraction/prompts/v4`, `llm/stub.py` | ✅ |
| R1.6 | Normalize: invoice type from the document title (credit memo / note → `CREDIT`, all amounts made negative, flag `sign_from_document_type`; a supplier debit note is an extra charge and stays `STANDARD`, since an EBS Debit Memo is raised by the buyer); several taxes → total tax = their sum; price basis (`/M`, `per 1000`, `¢`, or inferred when quantity × price is 100× / 1000× the amount) converted to a per-unit price; implausible bank numbers (all zeros, fewer than 5 digits) dropped; freight / shipping / handling lines → `FREIGHT`; Canada country hint (postal code, province, GST/HST/QST/TPS/TVQ) → CAD | `normalize/` | ✅ |
| R1.7 | Map: one TAX line per printed tax (when the rows add up to the tax amount); `location_ref` → `cost_center` through value maps (`map_type = location`, by the full text, then by its number alone; loaded from the functional team's table in R2.1). GL coding does not use `cost_center` yet (R2.1) | `mapping/map.py` | ✅ |
| R1.8 | Validation: C4 only with a rate that is on the page, and per tax row when several are printed; C3 counts printed freight rows; C11 also fails on a printed bank-change notice (fraud); new C13 invoice type vs sign; V-05 warns on `terms_due_date_conflict` | `validation/rules/` | ✅ |
| R1.9 | Interface payload takes the invoice type from the canonical invoice, not only from the sign | `adapters/ebs_mock.py` | ✅ no change needed: the payload already uses the canonical type; C13 blocks a CREDIT with a positive total |
| R1.10 | APLOAD CSV export in Dhivya's format: `aap.submit.apload` (file for a set of approved invoices) and `GET /api/documents/{id}/apload` (one invoice); golden-file test built from the sample | `submit/apload.py`, `api/main.py` | ✅ |
| R1.11 | Tests for R1.2–R1.10; re-run the 5 samples on the real model; regression eval on `invoices-dev-55` with prompt v4 | `tests/unit`, eval | ✅ (§6) |

## 3. Phase R2: needs the functional team

| # | Item | Waiting on | Status |
|---|---|---|---|
| R2.1 | Location → GL segment 3: load the store ↔ cost-center table as `location` value maps; GL coding takes the cost center from the location and the natural account from history | Store ↔ cost-center table, meaning of all 8 segments | ⏸ |
| R2.2 | Mock chart of accounts → 8 segments with `.` like theirs (seed, GL tools, scenarios) | Segment meanings | ⏸ |
| R2.3 | Seed the real suppliers of the samples (Level Access, Mspark, Énergir, N Wasserstrom, Corral, Bruin 22662) so supplier, site and OU mapping run on real invoices | Supplier numbers and sites | ⏸ |
| R2.4 | Invoice source for our invoices (sample uses `BRUIN`); submit mode: open interface vs APLOAD file | Source name, delivery of the file | ⏸ |
| R2.5 | Delivery tickets (Corral): one line at the total or every product | How they key them today | ⏸ |
| R2.6 | GL date policy default | Vikram | ⏸ |
| R2.7 | Invoice-type scope (credit / debit memos in phase 1?) | Dhivya | ⏸ |

## 4. Later phases (unchanged)

- **R3 Real EBS** (earlier C1–C5): read adapter (python-oracledb), reference sync on their instance, terms value
  maps with the functional team, write adapter, shadow run against manually keyed invoices.
- **R4 Model**: measure PO-line pairing; LoRA for extraction only if the real gold set misses the gates. The
  `Downloads\AllAttachments_New` / `EmailBodyAndAttachment` folders may be a real AP inbox export: if so, they are
  the gold set.

## 5. Verification

Unit tests per item; the 5 samples re-run on the real model (before / after table in §6); `invoices-dev-55` eval
with prompt v4 must still pass both PLAN 4 gates (header ≥ 97 %, line F1 ≥ 95 %).

## 6. Results (2026-10-26)

**Tests:** 83 unit tests (28 new, incl. a byte-for-byte APLOAD round trip of the sample's second invoice and
the worker recovery against Procrastinate's in-memory connector); e2e S1-S15 with the stub LLM 23 passed,
3 skipped (embeddings, as before).

**Regression eval, `invoices-dev-55`, real model** (run `prompt-v4-final-dev55-agents`):

| | v3 (`prompt-v3-dev55-agents`) | v4 |
|---|---|---|
| Header accuracy (gate ≥ 97 %) | 99.27 % | 99.3 % |
| Line F1 (gate ≥ 95 %) | 100 % | 100 % |
| `po_number` | 94.5 % | 100 % |
| Validation outcome (fails) | 96.4 % | 98.2 % |
| `bank_account`, `iban`, `vendor_name` | 98.2 % | 100 % |
| Line values not on the invoice | 4 | 2 |
| `extract.subtotal` | 98.2 % | 96.4 % (two German "Zwischensumme" invoices read as total only; the header subtotal is derived from the lines and stays 100 %) |

Two intermediate v4 drafts are in `eval_run` too (`prompt-v4-dev55-agents`, `prompt-v4b-dev55-agents`): the first
dropped "Order No." from the PO labels and its price-basis sentence made the model copy amounts into unit
prices (14 invented values); both were fixed in the prompt and by a normalize guard.

**The 5 samples, real model, before (v3, 2026-10-23) → after:**

| Invoice | Fixed | Still open |
|---|---|---|
| Level Access | Terms 60 NET from the due date (invented "Net 30" ignored); C11 fails on "use new routing number" | Supplier / PO not in the mock EBS (R2.3) |
| Mspark | Price per 1000 → per unit (C2 passes); invented 8 % rate and its false C4 gone; misread phone / P.O. box as bank details dropped (ABA check); `STORE 316` read | PO read from "Insertion Order TNB01818" (V-PO fails); 17-day due date has no matching term (no_map, by design) |
| Énergir | CAD instead of USD; cents prices → per unit (C2 passes); invented "12 %" rate no longer passes the source check | Only one of the two taxes (GST, not QST) is read, so C5 fails: the misread is caught, not fixed |
| N Wasserstrom | PO 125679 from "Your order number" (was "Mark For"); freight line → FREIGHT; invented bank account / routing / IBAN dropped (false C11 gone); `KRS 51` read | Due date copied from the invoice date (shown as a terms conflict); PO lines from the line-number column |
| Corral | No invented due date | Lines unusable (R2.5) |

**Found on the way:** the source check matched a percentage without its `%` sign ("12%" found in a date);
fixed in `extraction/source_check.py`.
