# AAP Module: Plan to Mid-December 2026

| Item | Value |
|---|---|
| Status | Active |
| Started | 2026-11-03 |
| Target | Non-PO invoices end to end on a replica of the Krispy Kreme setup (no EBS access before December), tested against what AP keyed, by **2026-12-15** |
| How the data is used | [IMPLEMENTATION-PLAN-CLIENT-DATA.md](IMPLEMENTATION-PLAN-CLIENT-DATA.md) |
| Input | Call with Siva, Jayesh, Ketan and Vikram (2026-11-02); data request sent to Vikram and Dhivya (2026-11-03) |
| Builds on | [IMPLEMENTATION-PLAN-STATUS.md](IMPLEMENTATION-PLAN-STATUS.md) (phases 0-10), [PLAN-REAL-INVOICES.md](PLAN-REAL-INVOICES.md) (R1, R1b done; R2 open), [LLM-USAGE-BY-STAGE.md](LLM-USAGE-BY-STAGE.md), [FINETUNING-SCOPE.md](FINETUNING-SCOPE.md) |

Legend: ✅ done and verified · 🟡 done, needs real data to confirm · 🟠 in progress · ⬜ not started · ⏸ waiting on input · ❌ dropped

This is the tracking document until mid-December. Update the status columns as items move, and add a line to
§10 for every decision.

## 1. What is expected by mid-December

Stated by Siva on 2026-11-02:

- **One call, invoice in → JSON out.** The JSON holds everything Oracle needs to create the invoice, so a
  program can call the Oracle API or load the open interface directly. No manual steps between the model and
  Oracle ("I don't want a 2-3 level process").
- **Non-PO invoices first.** The pain point is the expense account: today AP chases the requesting department
  for the GL account on every non-PO invoice. The liability account defaults from the supplier site and is not
  part of the problem.
- **Learn from history.** For a non-PO line, the GL combination comes from the supplier's past invoices
  (confirmed when Vikram asked).
- **Oracle setup is known to the system:** invoice types, line types (item, freight, tax), payment terms,
  payment methods, suppliers and sites, chart of accounts.
- **Start with 10-25 suppliers.** If it works for those, more suppliers are a matter of loading their data.
- **JSON only.** The EBS-friendly CSV is not needed ("just send me the JSON file"; Siva will talk to Dhivya).
- **Data:** Vikram extracts supplier, LOV and account data from the Krispy Kreme instance and sets up those
  suppliers in the internal instance. Dhivya pulls about 500 non-PO supplier invoices (attachments in
  `FND_LOBS`) across about 10 suppliers.

### Definition of done (2026-12-15)

1. A non-PO invoice PDF from one of the selected suppliers, uploaded to the application, produces an
   Oracle-ready JSON (§5, workstream A) with supplier, site, invoice type, terms, lines with line types, and an
   expense account on every item line.
2. There is no EBS access before December, so the mock EBS is reloaded from the Krispy Kreme extracts (the
   "replica"). Approving the invoice creates it there through the mock Payables Open Interface Import, and on
   held-out invoices the JSON is compared field by field with what AP keyed (shadow comparison). Creating it in
   a real instance is done on their side with the JSON.
3. Measured on held-out real invoices that were not used for training or tuning:
   - header fields correct: target ≥ 97 % (same gate as the synthetic eval);
   - expense account correct on non-PO lines: target agreed with Siva after the baseline (§5, workstream C);
   - share of invoices that need no correction at all, reported per supplier.
4. Invoices the system is unsure about go to review with a reason. Nothing is posted on a guess.

## 2. How the application meets the expectation

Siva describes the target as "a trained model". For him the application is one call; inside, each part of the
work is done by what does it reliably:

| Part of the job | Done by | Why not by training the model on it |
|---|---|---|
| Reading the invoice (supplier name, number, date, amounts, lines, taxes, store) | Vision model (Qwen2.5-VL-7B); **fine-tuned on the real invoices** if the baseline shows reading errors | This is the right place for training: same skill on every invoice, stable over time |
| Supplier, site, terms, payment method, invoice type, line type | Oracle reference data loaded from the instance, matched by code; the model only picks from a shortlist when matching is unclear | Oracle values change (new suppliers, new terms); a lookup is current the same day, a trained model needs retraining |
| Expense account on a non-PO line | The supplier's own history of keyed distributions (same text, then similar text by embeddings), cost center from the store | A new supplier or account change would need retraining; history grows with every approved invoice |
| Oracle-ready JSON and the push to Oracle | Code (interface payload, adapter) | Must be exact and auditable |

This is the answer to "if it works for one supplier, it works for 100": each new supplier needs its data
loaded, not a new model.

## 3. Completed

Everything below is built, tested and committed or in the working tree. Details: the linked documents.

### 3.1 Platform (IMPLEMENTATION-PLAN phases 0-10)

| Area | What | Status |
|---|---|---|
| Runtime | Docker Compose: Postgres 16 + pgvector, vLLM (Qwen2.5-VL-7B FP8 on the RTX 5090), API (FastAPI), worker (Procrastinate queues), mock EBS concurrent manager | ✅ |
| Mock Oracle EBS | Postgres schemas faithful to R12: suppliers and sites, POs (standard and blanket with releases), receipts, terms, tax, chart of accounts and code combinations, AP history (invoices, lines, distributions, holds), open interface tables, a mock Payables Open Interface Import with rejections | ✅ |
| Intake | Upload (API and UI). The mailbox was removed on 2026-10-03 | ✅ |
| Pipeline stages 1-8 | Intake, unpack, parse (text layer + page images), route per page, extract, normalize, map, validate (rules C1-C13 and the PO and reference checks) | ✅ |
| Agents | Triage, find PO, pair lines, three-way match, holds, GL coding, finalize (LangGraph) | ✅ |
| Submit and result | Interface rows → import → result poll; GL history fed back on approval | ✅ |
| Review UI | Document list, invoice page with corrections, approve / reject, value-map admin (minimal Jinja + HTMX) | ✅ |
| Audit | Every decision stored with its reason (`agent_decision`, `field_evidence`, validation history); audit check after each module | ✅ |
| Eval | Harness with field-level accuracy, gates (header ≥ 97 %, line F1 ≥ 95 %), `model_use` report | ✅ |
| Fine-tuning export | `aap.finetune.export`: `extraction.jsonl` (page images + corrected JSON) and `choices.jsonl` | ✅ mechanism; no real labels yet |

### 3.2 Real-invoice readiness (PLAN-REAL-INVOICES R1, R1b)

From the 5 Krispy Kreme sample PDFs and the `APILOAD` file:

| Area | What | Status |
|---|---|---|
| Stability | PDFium lock (parallel parse crash), recovery of jobs left by a crashed worker | ✅ |
| Extraction | Prompt v5: document type, store / location, every tax row, bank-change notice, PO per line; no computed or invented values (terms, due date, bank details); tax re-read when the taxes do not add up | ✅ |
| Normalize | Credit memo from the title (amounts negative), several taxes, price per 1000 / in cents, freight rows → `FREIGHT`, Canada → CAD, implausible bank numbers dropped | ✅ |
| Map | Terms from the due date when none are printed; one TAX line per tax row; store → cost center (value maps `location`); GL date policy | ✅ |
| Validation | Tax per row, freight in the total check, bank-change notice fails C11, invoice type vs sign (C13), terms vs due date warning | ✅ |
| PO | PO per line, several POs per invoice, blanket releases | ✅ (mostly PO-side; not the December focus) |
| Non-PO GL + store | When the store maps to a cost center, it replaces the cost-center segment of the account found from history; combination checked live | ✅ mechanism; store table missing (R2.1) |
| Export | JSON, standard CSV, ZIP per document and in bulk; APLOAD file in Dhivya's format | ✅ (CSV/APLOAD no longer required, §10) |
| Results | Synthetic eval on prompt v5: header 99.3 %, line F1 100 %, 0 invented line values. 114 unit tests, 10 integration, stub e2e S1-S15 | ✅ |

### 3.3 Analysis and documents

| Item | Status |
|---|---|
| Where the LLM is used, stage by stage, and the fine-tuning scope of each ([LLM-USAGE-BY-STAGE.md](LLM-USAGE-BY-STAGE.md)) | ✅ |
| Fine-tuning and mapping reasoning for business and leads (Claude Doc "Fine-tuning and mapping in the AP pipeline") | ✅ |
| Analysis of Dhivya's `pay_terms.xlsx` (73 terms) and `pay_method.xlsx` (48 methods): value lists, not training data; used as reference data | ✅ |
| Data request to Vikram and Dhivya for the December plan | ✅ drafted 2026-11-03 |
| Analysis of Vikram's `AI AP Excel.xlsx` (2026-11-04): mandatory Workbench fields (used for the JSON); the LOVs are the internal **Vision demo** instance, not Krispy Kreme (§6) | ✅ |

### 3.4 Work that needed no client data (2026-11-04)

Unit tests: 160 pass (114 before). Integration: 12 pass (2 new). E2E in the app container on a throwaway
database: 22 pass, 3 skip (embeddings, as before), 1 fails: S9, which fails the same way on the committed
code (see §9).

| Item | What was built | Where |
|---|---|---|
| A1 Oracle-ready JSON | One JSON per invoice from the same payload the adapter posts: business keys in `header`, instance IDs apart in `source_ids`, one distribution per non-PO line, `status` (ready / needs review, `postable`, missing required fields per Vikram's list, uncertain values with reasons). Before approval: the reviewed version with the agents' PO and GL decisions | `pipeline/oracle_json.py`, `adapters/payload.py` |
| A2 Endpoint, schema, samples | `GET /api/documents/{id}/oracle.json`, `GET /api/schemas/oracle-invoice.json`, `oracle_invoice.json` in every export ZIP; JSON Schema, a made-up sample and a guide for whoever writes the push (a unit test keeps them in step) | `api/main.py`, `docs/ORACLE-JSON.md`, `docs/oracle-invoice.schema.json`, `docs/oracle-invoice.sample.json` |
| A3 Payment fields | Payment method, pay group, payment currency, terms date on the invoice: supplier site default, else supplier; terms date by the site's terms date basis (setting `TERMS_DATE_BASIS`). New interface columns posted (vendor number / name, OU name, terms date, payment method, pay group, payment currency) | `mapping/map.py` `map_payment`, `schemas/canonical.py` |
| D2 Chart of accounts layout | Segment count, separator, company / cost-center / natural-account segment are settings (`GL_SEGMENT_COUNT`, `GL_SEGMENT_SEPARATOR`, `GL_*_SEGMENT`); one helper instead of `split("-")` in the adapter, GL tools, approval feedback, fine-tuning export, CLI and mock import. Mock EBS: `FND_ID_FLEX_STRUCTURES` / `_SEGMENTS` (delimiter and value sets), KFV view by delimiter, unique index over 8 segments; `ref_ccid` keeps every segment and the natural account (migration 0004). A seeded database gets the new objects on the next migrate (`additions.sql` is re-applied), no reseed needed | `common/coa.py`, `ebs_mock/ddl/additions.sql`, `adapters/ebs_mock.py` |
| B2 Payment methods | Mock `IBY_PAYMENT_METHODS_B/_TL` (synthetic: CHECK, EFT, WIRE), reference copy `ref_payment_method`, unknown code flagged | `additions.sql`, `refsync/sync.py` |
| B1 Terms | Dhivya's 73 terms read from her export; term lines derived from the names where `AP_TERMS_LINES` are missing (72 of 73; `90 EOM` left unknown), replaced by the real lines when they arrive | `clientdata/terms.py` |
| B8 Terms matching | Same days → the supplier default's family (`30 NET` vs `30 DIAS`); `CONTADO` / "de contado" = immediate; end of month by day (`15 EOM`, "15th after month end", "EOM 15"); discount terms in KK wording (`30 NET 2%10`, "Net 30, 2% 10 days") | `mapping/map.py` |
| D1 Intake and checker | Readers for CSV / XLSX incl. SQL Developer exports and `_TL` languages; checks for files, columns, keys, joins (attachment ↔ invoice, line → invoice, distribution → line and account, supplier, site, terms, payment methods, selected suppliers, store cost centers), totals, reversals, segment count, PO-matched invoices; per-supplier summary with scanned PDFs; `manifest.json` + `check-report.md`; made-up fixture delivery | `clientdata/` (`check`, `readers`, `tables`, `fixtures`), `client-data/README.md` |
| C1 Gold set | Attachment + keyed record → eval truth (keyed values at mapped level, only the printed values the record gives); split test / dev / train with held-out suppliers; `history_exclude.json` for the replica | `clientdata/goldset.py` |
| Eval | A truth key that is absent is not scored (partial truth; all 570 synthetic truths unchanged); keyed `terms_id`, `invoice_type`, `payment_method` scored when present; `--split` | `eval/metrics.py`, `eval/harness.py` |
| A5 Instance value map | KK value → target instance value per kind (supplier, site, terms, payment method, OU, account / segment), identity by default; values a map does not cover are listed in the JSON | `mapping/instance.py`, setting `INSTANCE_MAP_FILE` |
| Line types | Vikram's freight examples: courier charges, delivery fee, Spanish *flete* → `FREIGHT`. Fees are not made `MISCELLANEOUS` until B-7 says how KK keys them | `normalize/normalize.py` |

## 4. What is already real, and what is still mock

| Part | Real product today | Still mock or synthetic | Becomes real through |
|---|---|---|---|
| Pipeline, agents, rules, review UI | ✅ the code that will run in production | – | – |
| Extraction model | ✅ real model on real PDFs (5 samples) | Accuracy measured on synthetic invoices only | Workstream C (baseline on the 500) |
| Oracle reference data | Shape and queries are R12 (`AP_TERMS`, `PO_VENDOR_SITES_ALL`, `GL_CODE_COMBINATIONS`, …) | Values: mock suppliers, 12 mock terms, 4-segment mock chart of accounts | Workstream B (load the extracts) |
| GL history | Lookup + embeddings + cost center from the store | Synthetic history | Workstream B (distributions of the selected suppliers) |
| Interface payload | Same columns as `AP_INVOICES_INTERFACE` / `AP_INVOICE_LINES_INTERFACE` | Written to the mock interface | Workstream A (the Oracle-ready JSON is built from it) |
| Oracle import | Mock Payables Open Interface Import with real reject codes | Not the real program | Their push program; for December the mock import on the replica (D) |
| Read adapter | Interface `ErpReadPort`; the SQL is written to be reusable with python-oracledb | Reads the mock Postgres | After December, when read access exists |
| Training data | Export mechanism | 0 real labelled invoices | Workstream C (the 500 with their keyed values) |

## 5. Pending work

### A. Oracle-ready JSON (no outside input needed)

| # | Item | Where | Status |
|---|---|---|---|
| A1 | Oracle-ready JSON per invoice: header and lines shaped like the interface, by business key; review status and the reasons for anything not certain. Built from the same payload the adapter writes, so the JSON and what is posted cannot differ (§3.4) | `pipeline/oracle_json.py`, `adapters/payload.py` | 🟡 |
| A2 | API endpoint, JSON Schema, sample, guide for the push (§3.4) | `api/main.py`, `docs/ORACLE-JSON.md` | ✅ |
| A3 | Payment method in the JSON: supplier site default, else supplier (built). Whether AP keys a printed one instead is still open | `mapping/map.py` | 🟡 default done; ⏸ B-6 |
| A5 | Instance value map, KK → target instance values; identity until Vikram says the values differ (§3.4) | `mapping/instance.py` | ✅ mechanism; ⏸ B-0 |
| A4 | Stop extending the CSV / APLOAD exports; keep them working, no new work | – | ✅ decided |

### B. Load the Krispy Kreme data

| # | Item | Where | Status |
|---|---|---|---|
| B1 | The 73 real payment terms and their lines as replica rows (lines derived from the names until `AP_TERMS_LINES` arrive); written into `ebs_kk` by the replica loader (D3) | `clientdata/terms.py` | 🟡 rows ready; real lines ⏸ B-5 |
| B2 | Mock `IBY_PAYMENT_METHODS`; the 48 methods as replica rows | `additions.sql`, `clientdata/terms.py` | ✅ |
| B3 | Loader for Vikram's extracts: suppliers, sites, org, defaults (terms, payment method, liability account) | `ebs_mock/seed/` or a new loader | ⏸ data |
| B4 | Chart of accounts → 8 segments with `.` like theirs; valid code combinations (R2.2). The layout is built (D2); the values and segment meanings are not here | replica loader | ⏸ B-2 |
| B5 | Store ↔ cost-center table loaded as `location` value maps (R2.1) | `python -m aap.mapping.locations` | ⏸ table |
| B6 | GL history: non-PO distributions of the selected suppliers into the mock AP history → `gl_history_line`, embeddings | refsync, embed backfill | ⏸ data |
| B7 | LOVs: invoice types, line types, invoice source (R2.4), in-scope invoice types (R2.7). Loader, reference copy and rule V-LOV built 2026-11-04: Dhivya's workbook goes in with `python -m aap.clientdata.lov <xlsx> --out <delivery>` (P3.3) | `clientdata/lov.py`, `common/lookups.py`, `validation/rules/r14_lookups.py` | ✅ built; ⏸ Dhivya's list (Fri 11-06) |
| B8 | Terms matching fixes from the real list: end of month by day, NET vs DIAS by supplier default, CONTADO, KK discount wording | `mapping/map.py`, tests | ✅ |

### C. Real-invoice gold set, baseline and fine-tuning

| # | Item | Where | Status |
|---|---|---|---|
| C1 | Gold-set builder: pair each PDF (named by `invoice_id`) with its keyed header, lines and distributions; write ground-truth files in the eval format; the harness scores partial truth (§3.4) | `clientdata/goldset.py`, `eval/` | ✅ on fixtures |
| C2 | Split: held-out suppliers + most recent ~15 % → test, ~10 % → dev, rest → train; built into C1. To run on the real delivery | `clientdata/goldset.py` | 🟡 rule built; ⏸ data |
| C3 | Baseline on the held-out set with the current model and prompt v5: per-field accuracy, line F1, GL accuracy on non-PO lines, no-touch rate per supplier | `aap.eval` | ⏸ data |
| C4 | Prompt fixes from the baseline errors (cheap; before any training) | `extraction/prompts/v6` | ⬜ |
| C5 | Fine-tune extraction (QLoRA, vision encoder frozen, same prompt and schema) on the training split, only if C3/C4 miss the gate; vLLM stopped while training | `finetune/` | ⬜ decide after C3 |
| C6 | Re-run the held-out eval with the adapter; also re-check the choice nodes, since the same model answers them | `aap.eval` | ⬜ |
| C7 | GL thresholds (`GL_SIM_AUTO`, history rules) tuned on the real history, measured on the held-out set | `config.py` | ⬜ |
| C8 | Agree the GL accuracy target with Siva from the baseline | – | ⬜ |

### D. Replica and checks without EBS access

No EBS access (Krispy Kreme or internal) is expected before December. Details: IMPLEMENTATION-PLAN-CLIENT-DATA
P1, P2, P9.

| # | Item | Where | Status |
|---|---|---|---|
| D1 | Intake folder outside git, checker for the extracts (joins, totals, manifest), made-up fixtures (§3.4) | `src/aap/clientdata/` | ✅ |
| D2 | 8-segment chart of accounts and payment methods in the mock (§3.4) | `common/coa.py`, `ebs_mock/`, `adapters/`, `config.py` | ✅ |
| D3 | Replica loader: `ebs_kk` from the extracts, `aap_kk` by reference sync; test invoices, held-out suppliers and history after the cut-off kept out (`history_exclude.json` from C1). Built for every extract (OUs and ledgers, chart of accounts, terms, payment methods, suppliers and sites, invoices / lines / distributions with reversals) and tested end to end on the fixture delivery, including an import with an 8-segment account. **Loaded 2026-11-04** from Dhivya's files: 73 terms (72 with lines from the names), 48 payment methods; no suppliers, accounts or history yet | `ebs_mock/client_load.py`, `clientdata/replica.py` | 🟡 loaded with what exists; reload as B-1, B-2, D-2, D-3 arrive |
| D3a | `ebs_kk` / `aap_kk` databases next to `ebs` / `aap` (same roles and grants), `.env.kk` / `.env.kk.example` | `clientdata/replica.py --create-databases` | ✅ |
| D4 | Shadow comparison: JSON vs keyed record on the test split. Built 2026-11-04 with the GL metrics (C7's measurement) and the per-supplier report; `python -m aap.eval --dir <gold set> --split test --agents` | `eval/shadow.py`, `eval/gl.py` | ✅ built; ⏸ data |
| D5 | Mock import of the test split on the replica. The import now reads the account delimiter and segment count of the chart, and takes the terms date and payment currency from the interface row | `ebs_mock/import_program.py` | 🟠 import ready; ⏸ replica |
| D6 | Live EBS (python-oracledb adapter, refsync, push) | – | ❌ after December |

### E. Hardening for the demo

| # | Item | Status |
|---|---|---|
| E1 | Determinism check under concurrent load (same PDF, same answer), see PLAN-REAL-INVOICES §7 | ⬜ |
| E2 | Real client data handling: where the PDFs and extracts are stored, who can access them, deletion after the project; written approval for training use | ⏸ Jayesh |
| E3 | Update HOW-IT-WORKS.md section 1 (still lists the removed mailbox LLM uses) and IMPLEMENTATION-PLAN-STATUS (phase 5 mailbox) | ⬜ |
| E4 | Demo script and results summary for Siva | ⬜ |

## 6. Inputs awaited

Requested on 2026-11-03. The full explanation of each item is in the email; this is the tracker.

| # | From | Input | Unblocks | Asked by | Status |
|---|---|---|---|---|---|
| D-1 | Dhivya | ~500 non-PO invoice attachments from `FND_LOBS`, each file named by `invoice_id` | C1-C6 | 2026-11-10 | ⏸ |
| D-2 | Dhivya | Keyed header, lines and distributions (`AP_INVOICES_ALL`, `AP_INVOICE_LINES_ALL`, `AP_INVOICE_DISTRIBUTIONS_ALL`) for the same `invoice_id`s | C1-C6, B6 | 2026-11-10 | ⏸ |
| D-3 | Dhivya | 2 years of non-PO distributions of the selected suppliers (no PDFs) | B6, C7 | 2026-11-10 | ⏸ |
| B-0 | Vikram (+ Siva) | **Which values the JSON carries.** The internal instance's setup is meant to copy KK's. Does Vikram recreate KK's values there (supplier numbers, site codes, terms, payment methods, 8 segments; recommended), or re-map them (then the KK → internal map for every kind)? To confirm on the 2026-11-04 call | A5, D3, B4 | 2026-11-04 | ⏸ new |
| B-1 | Vikram | Selected suppliers: supplier and site master, org, defaults; send the KK extracts used for the internal setup | B3 | 2026-11-04 | ⏸ not in the 11-04 sheet |
| B-2 | Vikram | 8 GL segments with meanings, valid values, code combinations used by these suppliers; cross-validation rules | B4 | 2026-11-04 | ⏸ the sheet has Vision's company and department only |
| B-3 | Vikram | Store ↔ cost-center table | B5 | 2026-11-04 | ⏸ |
| B-4 | Vikram | Invoice types and which are in scope; line types; invoice source; OU names | B7 | 2026-11-04 | 🟠 types and line types listed (Vision values); scope, source, OU open |
| B-5 | Vikram | `AP_TERMS_LINES` for the 73 terms | B1 | 2026-11-04 | ⏸ (derived from the names meanwhile) |
| B-6 | Vikram | Payment methods in use (`IBY_PAYMENT_METHODS_B`), keyed per invoice or defaulted | A3, B2 | 2026-11-04 | ⏸ the sheet lists 6 Vision methods |
| B-7 | Vikram | How tax and freight are keyed on non-PO invoices; tax setup (E-Business Tax?) | A1, B7 | 2026-11-04 | ⏸ |
| B-9 | Vikram | Business rules behind the mandatory fields: Trading Partner = supplier name? terms date basis, GL date policy, payment currency; which fields AP leaves to Oracle defaults | A1, A3 | new | ⏸ |
| B-10 | Vikram | A test import of 10-20 JSONs into the internal instance once the suppliers are set up there | D5 | early December | ⏸ new |
| B-8 | Vikram | Push method (open interface + import, or API); instance access is not expected before December | A1 field names | 2026-11-07 | ⏸ |
| J-1 | Jayesh | Written approval to store and train on the client invoices | C5, E2 | 2026-11-07 | ⏸ |

Already received: 5 sample PDFs and `APILOAD_20251118.csv` (2026-10-23); `pay_terms.xlsx` and
`pay_method.xlsx` (2026-11-02); `AI AP Excel.xlsx` from Vikram (2026-11-04, `data_received/Vikram/`). It has
the Invoice Workbench mandatory fields (header, line, distribution; used for the JSON) and LOVs from the
internal **Vision demo** instance, not Krispy Kreme: Vision companies 01/02/70/99, 5 segments, Vision terms
and 6 payment methods, none of which matches Dhivya's KK terms. Its "invoice text → value" tables are made-up
examples, used only as test cases. None of B-1, B-2, B-3 and B-5 is covered.

## 7. Schedule

| Week | Work | Depends on |
|---|---|---|
| Nov 3-7 | Send the data request. A1-A2 Oracle-ready JSON. B1-B2 terms and payment methods. B8 terms fixes. D1 intake and checker, D2 8 segments. C1 gold-set builder on fixtures (**done 2026-11-04**, §3.4). B-0 answer | – |
| Nov 10-14 | D3 replica from the extracts; B3-B7 suppliers, chart of accounts, store table, LOVs, GL history. C1-C2 build and split the gold set | B-1…B-7, D-1…D-3 |
| Nov 17-21 | C3 baseline on the held-out set. C4 prompt fixes. C8 agree targets with Siva | Gold set |
| Nov 24-28 | C5-C6 fine-tune extraction if needed, re-measure. C7 GL thresholds | C3 result, J-1 |
| Dec 1-5 | D4 shadow comparison, D5 mock import on the replica; final re-run of the test split | Replica, gold set |
| Dec 8-12 | Results per supplier, E1-E4, demo | everything above |
| Dec 15 | Target date | |

Slack: none beyond the last week. A week of delay on D-1/D-2 removes a week of measurement or training;
fine-tuning (C5) is the first thing to drop.

## 8. Gates

| Gate | Measured on | Target |
|---|---|---|
| Header fields | Held-out real invoices | ≥ 97 % |
| Line F1 | Held-out real invoices | ≥ 95 % |
| Expense account on non-PO lines | Held-out real invoices | Set with Siva after C3 |
| Invented values (not on the page) | Held-out real invoices | 0 on header fields |
| Synthetic regression (`invoices-dev-55`) | After every prompt or model change | Both gates still pass |
| Interface import | Replica (mock import) | Every held-out invoice marked ready is accepted |
| Shadow comparison | Held-out real invoices | JSON equals the keyed record; share reported per supplier |

## 9. Risks

| Risk | Effect | Mitigation |
|---|---|---|
| Currencies other than the seed's (USD, EUR, GBP, JPY, CAD, AUD, SGD, CHF, AED) on the replica: MXN invoices (the fixture's Mexican supplier; KK Mexico if in scope) fail V-05, and need rates for V-FX | Mexican suppliers unmappable on the replica | Found 2026-11-04 by the replica eval. Add currencies (and `GL_DAILY_RATES`) from the delivery to the replica loader, or ask for FND_CURRENCIES / rates with the extracts |
| Data arrives late or the PDFs are not linked to their keyed records | No measurement, no training | Request says exactly what links them (`invoice_id`); C1 built in advance |
| 500 invoices from only 10 suppliers | A tuned model learns 10 layouts; new suppliers unproven | Ask for ~20 suppliers; hold out whole suppliers; report the held-out-supplier result separately |
| Expectation that "training the model" is the whole job | Disagreement at the demo | §2 explains the parts; report results per part |
| No EBS access before December | No import into a real instance | Replica of their setup + shadow comparison against what AP keyed; the JSON carries supplier number, site code and account string so their push works on any instance |
| GL history thin for some suppliers | Those lines go to review | 2 years of distributions (D-3); report review rate per supplier |
| Real client data on a dev machine | Compliance | Written approval and handling rules (J-1, E2) before training. `client-data/` and `test-data-kk/` are git-ignored (2026-11-04). `tests/unit/test_apload.py` (committed in October) holds real APLOAD rows (supplier, accounts): replace them with made-up values |
| Internal instance set up with values other than KK's (B-0) | Every keyed value must be translated; mapping errors look like model errors | Recreate KK's values (recommended); otherwise the instance value map (A5) |
| E2E S9 also fails on the committed code (2026-11-04): its ambiguous date resolves to a closed period (MAR-26), so V-GL fails before the date-ambiguity review | One red e2e test | Check the GL date policy against the seed anchor date; not caused by the 11-04 work |
| Training and serving share one 24 GB GPU | No extraction while training | Train overnight / in the Nov 24-28 window |

## 10. Decisions

| Date | Decision | By |
|---|---|---|
| 2026-10-03 | Mailbox out of scope; upload only | Team |
| 2026-11-02 | Non-PO invoices first | Siva |
| 2026-11-02 | Output is JSON; no EBS CSV needed | Siva |
| 2026-11-02 | Target: tested by mid-December, 10-25 suppliers, ~500 invoices | Siva |
| 2026-11-02 | Vikram sets up the selected suppliers in the internal instance; accounts either re-mapped to internal values or recreated there (his choice) | Siva, Vikram |
| 2026-11-03 | No EBS access before December: work on a replica loaded from the extracts; live EBS after December | Team |
| 2026-11-03 | Fine-tuning only for extraction, and only if the baseline on the real gold set misses the gates; Oracle setup and GL accounts stay as loaded data, not trained in | Team (see §2) |
| 2026-11-04 | Three data sources: the synthetic mock (tests and regression only, no more work on it), the KK instance (the truth: history, keyed invoices, PDFs; source of the setup) and the internal instance (target for creating invoices, connected after December). The replica holds KK's setup and history; the app is built on the KK extracts and does not wait for the internal setup | Team |
| 2026-11-04 | Records are joined by business key (supplier number, site code, account string, term name), never by ID, across instances; the JSON carries business keys, IDs only in `source_ids` | Team |
| 2026-11-04 | The CSV layout stays frozen; fields added since are in the JSON only | Team |
| 2026-11-04 | The replica lives in its own databases (`ebs_kk`, `aap_kk`); the synthetic `ebs` / `aap` are not touched by it. `ebs_kk` is rebuilt from the deliveries on every load; the loader refuses to write to `ebs` / `aap` | Team |

## 11. Out of scope until after December

- PO-matched invoices as the focus (built and kept working, not extended).
- Mailbox intake, vendor emails.
- CSV / APLOAD exports beyond what exists.
- Fusion or other ERPs.
- UI beyond the minimal review screens.
