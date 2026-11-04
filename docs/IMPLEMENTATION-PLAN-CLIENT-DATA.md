# AAP Module: Implementation Plan for the Krispy Kreme Data

| Item | Value |
|---|---|
| Status | Active; P1, P2.1-P2.2, P3.2, P4.1-P4.2, P9.1-P9.2 built on 2026-11-04 (PLAN-MID-DECEMBER §3.4) |
| Scope | How every file requested from Vikram and Dhivya (2026-11-03) is loaded, used and measured, without access to any EBS instance |
| Target | Non-PO invoices, mid-December 2026 ([PLAN-MID-DECEMBER.md](PLAN-MID-DECEMBER.md)) |
| Builds on | Mock EBS (`src/aap/ebs_mock`), eval harness (`src/aap/eval`), fine-tuning export (`src/aap/finetune`), [LLM-USAGE-BY-STAGE.md](LLM-USAGE-BY-STAGE.md), [FINETUNING-SCOPE.md](FINETUNING-SCOPE.md) |

Legend: ✅ done · 🟠 in progress · ⬜ not started · ⏸ waiting on data

## 1. Starting point and constraints

- **Everything runs on the dummy setup.** Oracle EBS is a mock in Postgres (`ebs` database) seeded with
  synthetic data (`aap.ebs_mock.seed`): ACSE companies, about 400 synthetic suppliers, a 4-segment chart of
  accounts (`01-510-6420-0000`), 12 terms, synthetic AP history.
- **No EBS access, and none expected before December**: not to the Krispy Kreme instance, and not reliably to
  the internal one. The data arrives as **files** (Excel / CSV extracts and invoice attachments).
- **Real client data.** Until now we kept real invoices out of the application on purpose. This plan runs them
  through it, so it needs the written approval (J-1 in PLAN-MID-DECEMBER) and the handling rules in §9.
- **Deliverable:** for each invoice, an Oracle-ready JSON (Siva, 2026-11-02). Creating the invoice in Oracle is
  done on their side ("Dhivya or somebody can write the code to call the API or push to the interface").

The consequence: **the mock EBS becomes a copy of the Krispy Kreme setup, loaded from their extracts**. The
application reads Oracle only through the adapter (`ErpReadPort`, `src/aap/adapters/base.py`), so the
pipeline, mapping, agents and review UI work on the copy unchanged. Accuracy is measured against what AP really
keyed, which is a stronger test than a live import: an import only proves the JSON is valid, the comparison
proves it is right.

## 2. The approach

```
 Dhivya / Vikram extracts            client-data/kk/<delivery>/   (outside git, read-only)
 (CSV / XLSX + PDFs)          ──►    raw files + manifest + checksums
                                              │
                         ┌────────────────────┼─────────────────────────┐
                         ▼                    ▼                         ▼
              P2 client replica        P4 gold set               P8 GL history
              ebs_kk database          PDF ↔ keyed record        (history minus test)
              (suppliers, sites,       train / dev / test        │
               terms, COA, CCIDs,      split                     │
               lookups, AP history)         │                    │
                         │                  ▼                    │
                         ▼            P5 baseline eval ◄─────────┘
              P3 reference sync            │
              aap_kk database              ├─► P6 labels ─► P7 fine-tune (only if needed)
              (ref tables, value maps,     │
               gl_history_line + vectors)  ▼
                         │            P9 Oracle-ready JSON
                         └──────────► + shadow comparison vs keyed record
                                      + mock import on the replica (contract check)
```

Two environments side by side, so nothing synthetic is lost:

| Environment | EBS database | App database | Used for |
|---|---|---|---|
| Synthetic (today) | `ebs` | `aap` | Unit, integration and e2e tests; the `invoices-dev-55` regression eval; demos without client data |
| Krispy Kreme replica | `ebs_kk` | `aap_kk` | Everything in this plan |

Switched by an env file (`.env.kk`: `EBS_*_DSN`, `APP_DSN`, GL segment settings, `TRUTH_DIR`, file store path).
Same containers, same code.

## 3. Every requested item: where it goes and what it is used for

| # | Item (email) | Lands in | Used by | Phase |
|---|---|---|---|---|
| — | List of chosen suppliers | `client-data/kk/suppliers.csv` | Filters every other extract; the split in P4 | P1 |
| A1 | ~500 invoice attachments, named by `invoice_id` | `client-data/kk/attachments/` → gold set `pdf/` | Extraction baseline, training images | P4-P7 |
| A2 | Keyed header / lines / distributions of those invoices | Gold set `truth/` (expected values); train / dev distributions also go into the replica AP history | Scoring every field; GL scoring; labels | P4-P8 |
| A3 | 2 years of non-PO distributions of the same suppliers | `ebs_kk.ap.ap_invoices_all`, `ap_invoice_lines_all`, `ap_invoice_distributions_all` → `gl_history_line` | GL coding from history | P2, P8 |
| B1 | Supplier and site master, OUs, ID mapping | `ebs_kk.ap.ap_suppliers`, `ap_supplier_sites_all`, `hz_parties`, `hr_all_organization_units` | Supplier / site mapping, defaults (terms, payment method, currency), the JSON | P2, P3 |
| B2 | 8 segments, values, code combinations | `ebs_kk.fnd.fnd_flex_value_sets` / `fnd_flex_values(_tl)`, `gl.gl_code_combinations` | Account validity check, account descriptions for the model, GL history | P2 |
| B3 | Store ↔ cost center | `aap_kk.value_map` (`map_type = location`) via `python -m aap.mapping.locations` | Cost center of non-PO lines | P3, P8 |
| B4 | Invoice types, line types, source | `ebs_kk.fnd.fnd_lookup_values` (`INVOICE TYPE`, `INVOICE LINE TYPE`), `SOURCE` setting | Invoice type and line type in the JSON | P2, P9 |
| B5 | `AP_TERMS_LINES` for the 73 terms | `ebs_kk.ap.ap_terms_tl` + `ap_terms_lines` (with `pay_terms.xlsx`) | Terms matching by wording and by due date | P2, P3 |
| B6 | Payment methods in use; per invoice or default | New mock table `iby.iby_payment_methods_b/_tl`; site default in `ap_supplier_sites_all` | Payment method in the JSON | P2, P9 |
| B7 | How tax and freight are keyed | Rules in `mapping/map.py` (TAX / FREIGHT lines, their accounts) | Lines of the JSON | P8, P9 |
| B8 | Push method; instance access | Not needed for December (§11) | Shape of the JSON (interface columns or API payload) | P9 |

## 4. Phase details

### P1. Data intake and checks

**Where the files live.** `client-data/kk/<delivery date>/` at the repository root, added to `.gitignore`,
never copied into `test-data/` or committed. Each delivery stays as received; a `manifest.json` written by the
checker records file names, row counts and SHA-256.

**Checker** `python -m aap.clientdata.check client-data/kk/<delivery>` (new module `src/aap/clientdata/`):

- reads CSV and XLSX (one sheet per table), maps the EBS column names case-insensitively;
- join checks, each reported as counts and the first offending IDs:
  - every attachment file name is an `INVOICE_ID` in the A2 header extract, and the reverse;
  - every A2/A3 line has its header; every distribution has its line and a `DIST_CODE_COMBINATION_ID`
    present in B2;
  - every `VENDOR_ID` / `VENDOR_SITE_ID` in A2/A3 is in B1 (or in the B1 ID mapping);
  - every `TERMS_ID` is in `pay_terms.xlsx`, every `PAYMENT_METHOD_CODE` in `pay_method.xlsx`;
- content checks: invoice amount = sum of lines; lines = sum of distributions (after dropping reversed
  pairs, `REVERSAL_FLAG = 'Y'` and the distribution they reverse); cancelled invoices listed and excluded;
- a per-supplier summary: invoices with attachments, invoices in the history, date range, scanned vs digital
  (pages without a text layer, from a quick PDF parse).

The report goes back to Vikram and Dhivya the same day if something does not join. **Nothing is loaded until
the checker passes or the exceptions are understood.**

| # | Item | Status |
|---|---|---|
| P1.1 | `client-data/` in `.gitignore`; folder layout; README with the handling rules (§9) | ✅ `client-data/README.md`; `test-data-kk/` ignored too |
| P1.2 | `aap.clientdata` readers (CSV / XLSX, column name mapping, dates, numbers with commas) | ✅ also SQL Developer exports ("Export Worksheet" + "SQL" sheets) and `_TL` languages; tried on Dhivya's two files |
| P1.3 | Checker with the join and content checks above; manifest | ✅ `python -m aap.clientdata.check`; also store cost centers, segment values vs value sets, segment count, PO-matched invoices |
| P1.4 | Fixtures with the same columns (made up, 3 suppliers) so P1-P4 can be built and tested before the data arrives | ✅ `python -m aap.clientdata.fixtures` (8 segments, reversal, cancelled and image-only invoice; `--broken`) |

### P2. Client replica of the mock EBS (`ebs_kk`)

**Loader** `python -m aap.ebs_mock.client_load --from client-data/kk/<delivery> --dsn <ebs_kk owner dsn>`
(new, next to `seed/`). It creates the schemas with the same DDL, then:

| Kept from the synthetic seed (generated) | Replaced by the extracts |
|---|---|
| `fnd_currencies`, `fnd_user`, concurrent programs, the AP tolerance template | Operating units, ledgers (USD and CAD), locations (B1) |
| `gl_period_statuses` for the history range and the coming months (all open) | Suppliers, parties, sites (B1) |
| `gl_daily_rates` (USD/CAD) | Terms and term lines (`pay_terms.xlsx`, B5) |
| Units of measure | Payment methods (`pay_method.xlsx`, B6) |
| Tax rates for US and Canada (until B7 says otherwise) | Chart of accounts: value sets, values, code combinations (B2) |
| | Lookups: invoice types, line types (B4) |
| | AP history: invoices, lines, distributions (A3 + the train / dev part of A2) |

PO tables stay empty for the Krispy Kreme OUs: non-PO is the December scope.

**IDs.** One ID space, decided by B1/B2: either Krispy Kreme's own IDs, or the internal-instance IDs if Vikram
re-maps them. The loader keeps whatever the extracts use and refuses a delivery that mixes them (checker).

**Test invoices are left out of the history.** The test split (P4) is removed from the AP history before it
is loaded, and the history is cut at the earliest test invoice date. Otherwise the GL lookup would find the
test invoice's own coding and the GL result would be meaningless.

**8-segment chart of accounts.** The mock assumes 4 segments joined by `-` in several places. Changes:

| Where | Change |
|---|---|
| `ebs_mock/ddl/gl.sql` | Unique index over `segment1..segment8` (the table already has 30 segment columns) |
| `ebs_mock/ddl/apps_views.sql` | `gl_code_combinations_kfv`: concatenate the configured number of segments with the configured separator |
| `adapters/ebs_mock.py` | Reference query (segment columns and descriptions), `ccid_valid` lookup, GL history (`natural_account` from the configured segment, not `segment3`) |
| `ebs_mock/import_program.py` (`_ccid_from_concat`) and `ebs_mock/cli.py` | Split by the separator into N segments |
| `db/models.py` `RefCcid` + migration | Keep `concatenated`; segments as a list (JSON) or segment1..8; descriptions per segment |
| `agents/tools/gl.py` | Account description by the natural-account segment |
| `config.py` | `GL_SEGMENT_COUNT` (mock 4, KK 8), `GL_SEGMENT_SEPARATOR` (`-` / `.`), `GL_COST_CENTER_SEGMENT` (mock 2, KK 3?), new `GL_NATURAL_ACCOUNT_SEGMENT` (mock 3, KK from B2) |

The synthetic environment keeps 4 segments and `-`, so all existing tests stay as they are; new tests cover
8 segments with `.`.

**Invariants.** `seed/invariants.py` checks are run against the replica too (every site has a supplier,
every distribution a valid CCID, period open for every history date, …).

| # | Item | Status |
|---|---|---|
| P2.1 | Settings for the segment count, separator, natural-account and cost-center segment; 8-segment changes in the files above; tests with an 8-segment fixture | ✅ `common/coa.py`; delimiter in `FND_ID_FLEX_STRUCTURES` (`ebs_mock/ddl/additions.sql`); `ref_ccid.segments` / `natural_account` (migration 0004) |
| P2.2 | Mock `IBY_PAYMENT_METHODS_B/_TL`; payment method on the site reference data | ✅ plus pay group, payment currency and terms date basis on the site |
| P2.3 | `client_load`: foundation from the seed, the rest from the extracts; history without the test split | ✅ `aap.ebs_mock.client_load` + `aap.clientdata.replica`; tested on the fixtures (`tests/integration/test_replica_load.py`); loaded 2026-11-04 with Dhivya's terms and payment methods; reload when the extracts arrive |
| P2.4 | `ebs_kk` / `aap_kk` databases, `.env.kk`, Makefile / script targets to load and switch | ✅ created by `python -m aap.clientdata.replica --create-databases`; switching the running stack to the replica (use `.env.kk`'s DSNs) not scripted yet |
| P2.5 | Invariants on the replica | ✅ the seed's checks run after every load (the S1 fixture check skipped); today only "open GL periods" fails, as there are no ledgers yet |

### P3. Reference sync and mapping on the replica

- `python -m aap.refsync` against `ebs_kk` fills `aap_kk` (suppliers, sites, terms, CCIDs, lookups) and copies
  the AP history into `gl_history_line`; the embedding backfill job then embeds every history line (bge-m3 on
  CPU; about 2 years × 20 suppliers is expected to be tens of thousands of lines, a few hours once).
- Store table (B3): `python -m aap.mapping.locations stores.csv` into `aap_kk`.
- Terms matching fixes found in `pay_terms.xlsx`: end of month by day, `NET` vs `DIAS` names by the supplier's
  default, `CONTADO` = immediate. With the term lines (B5) the due-date matching uses the real days.
- Invoice type and line type codes from the B4 lookups instead of the hard-coded mock values.
- Supplier matching: the B1 names, alternate names and tax registration numbers. Every supplier in the gold
  set is in the replica, so supplier mapping is measured for real (the current "none" answers on the 5 samples
  were caused by the suppliers missing from the mock).

| # | Item | Status |
|---|---|---|
| P3.1 | Refsync and embedding backfill on `aap_kk`; timing noted | ⏸ data |
| P3.2 | Terms fixes + tests | ✅ supplier family (NET / DIAS), CONTADO, end of month by day, KK discount wording; term lines from the names until B5 |
| P3.3 | Invoice / line type codes from lookups | ✅ (2026-11-04) Oracle's seeded codes and meanings in `common/lookups.py`; a list-of-values workbook (both layouts: field names on top, or titled sections) → `fnd_lookup_values.csv` with `python -m aap.clientdata.lov`; the replica replaces the seeded rows of each delivered lookup type; refsync → `ref_lookup` (migration 0005); rule **V-LOV** fails an invoice type, line type or invoice source not in (or disabled in) the instance's list |
| P3.4 | Store table loaded | ⏸ B3 |

### P4. Gold set: each PDF with what AP keyed

**What AP keyed is not always what is printed.** The keyed record is the right answer for Oracle, but not
always a verbatim copy of the page, and the extraction model is asked for printed values. Field by field:

| Field | Keyed value vs printed value | Used for |
|---|---|---|
| Invoice number, invoice date, total, currency | Same value (format aside) | Scoring and labels |
| Supplier | `VENDOR_ID`, not the printed name | Scoring at the mapped level (`header.vendor_id`) |
| Site | `VENDOR_SITE_ID` | Scoring at the mapped level |
| Tax total | Same value when tax is keyed as lines or a header amount (B7) | Scoring and labels |
| Terms | `TERMS_ID`, often the supplier default, not what is printed | Scoring at the mapped level only |
| Due date | From the payment schedule; derived by EBS, may not be printed | Scoring at the mapped level only |
| Lines | AP may key one line for the whole invoice or summarise; descriptions may be shortened | Lines scored only when the keyed lines match the printed ones (same count and amounts); otherwise header only |
| Distributions | The expense accounts; not on the page at all | GL scoring |
| Invoice type | Derived by AP | Scoring at the mapped level |

So the gold set is scored at two levels:

1. **Mapped level, against the keyed record, for every invoice.** This is what Siva cares about: is the JSON
   what AP would have keyed? Supplier, site, invoice number, date, total, tax, currency, terms, invoice type,
   line types, accounts.
2. **Extraction level, against printed values**, only for fields where the keyed value is the printed value
   (number, date, total, currency, tax), plus lines where they match. The rest of the printed values get labels
   in P6.

**Builder** `python -m aap.clientdata.goldset client-data/kk/<delivery> --out test-data-kk/<name>` (output
outside git like the raw data) writes the eval dataset format the harness already reads:

- `pdf/<invoice_id>.pdf`, `manifest.json`;
- `truth/<sha256>.truth.json` with:
  - `expected`: the keyed values at the mapped level (`vendor_id`, `vendor_site_id`, `org_id`,
    `currency_code`, `invoice_date`, `invoice_amount`, `tax_amount`, new: `invoice_num`, `terms_id`,
    `invoice_type`, `due_date`, `lines` with line type, amount and `dist_account`);
  - `source`: only the printed values that can be taken from the keyed record (invoice number, date, total,
    currency, tax); the other printed fields are left out, not set to null;
  - `invoice_id`, `vendor_number`, `supplier_holdout` flag, `scanned`, page count.

**Harness changes** (`src/aap/eval`):

- score a source field only when the truth has it (a missing key means "not labelled", not "should be empty");
- new mapped-level fields: `invoice_num` against the keyed value, `terms_id`, `invoice_type`, `due_date`,
  line types;
- GL metrics (P8);
- a report per supplier, and for the held-out suppliers separately;
- run with `.env.kk` so the pipeline maps against `ebs_kk`.

**Split** (written into the manifest, never changed once the baseline is run):

| Split | Size | Rule | Used for |
|---|---|---|---|
| Test | ~100 | The 1-2 held-out suppliers' invoices, plus the most recent ~15 % of every other supplier | Only for measuring; never used for prompts, training or thresholds |
| Dev | ~50 | Random from the rest | Prompt changes, thresholds, choosing the training checkpoint |
| Train | ~350 | The rest | Fine-tuning labels (P6) |

Test and dev invoices are excluded from the AP history in the replica (P2). Train invoices stay in it: they
are history the system would really have.

| # | Item | Status |
|---|---|---|
| P4.1 | Gold set builder on the fixtures (P1.4) | ✅ `aap.clientdata.goldset`; split and `history_exclude.json` included |
| P4.2 | Harness: partial source truth, new mapped fields, per-supplier report | ✅ partial truth, keyed `terms_id` / `invoice_type` / `payment_method`, `--split`; keyed line types (`lines.line_types`); per-supplier table and a held-out block in the report (2026-11-04) |
| P4.3 | Gold set and split from the real delivery | ⏸ A1, A2 |

### P5. Baseline: the current system on real invoices

Run on the **test** split with the current model, prompt v5 and the replica, agents included:

- extraction: invoice number, date, total, currency, tax (printed level); invented values;
- mapping: supplier, site, terms, invoice type (against the keyed record);
- GL on non-PO lines (P8 metrics);
- outcome: share of invoices with every field equal to the keyed record, and share sent to review, per
  supplier; time per invoice;
- every miss sorted into one of four causes, because each has a different fix:

| Cause | Example | Fix |
|---|---|---|
| Reading error | Total read from the wrong box, line amounts misread | Prompt (P5 → dev split), then fine-tuning (P7) |
| Mapping error | Right name read, wrong supplier or terms picked | Mapping rules, value maps, thresholds |
| Missing data | Store not in the store table, supplier history empty | Data from Vikram / Dhivya |
| AP keyed it differently | AP used the supplier default terms, or one summary line | Not an error; documented, scoring adjusted |

Prompt changes from the reading errors are made against the **dev** split (prompt v6), then the test split is
run once more. The synthetic `invoices-dev-55` regression must still pass both gates.

| # | Item | Status |
|---|---|---|
| P5.1 | Baseline run on test; report with the cause of each miss | ⏸ P4.3 |
| P5.2 | Prompt v6 against dev; re-run test and the synthetic regression | ⬜ |
| P5.3 | Results and the GL target agreed with Siva (PLAN-MID-DECEMBER C8) | ⬜ |

### P6. Training labels (only for the train split)

Fine-tuning needs the **printed** values for every field the model returns. The keyed record gives only some
of them (P4 table). The rest come from a quick human check, using the review screen we already have:

1. Upload the train invoices with source `kk-train` (not `eval`: the export leaves eval documents out).
   The real model extracts them as usual.
2. A script compares each extraction with the keyed record:
   - fields where the model's value equals the keyed value are accepted;
   - fields where they differ, plus fields the keyed record does not cover (supplier name and address as
     printed, store, terms as printed, PO, lines when they cannot be matched), are listed for review;
   - an invoice with no differences and lines that match the keyed lines is accepted as a whole.
3. The listed invoices are checked on the invoice page and corrected with the existing correction form
   (`aap.submit.correct`). Each correction is stored as a `user` extraction run, which is exactly what
   `aap.finetune.export` writes as a "corrected" example.
4. Invoices that need no change are approved as checked. The export marks them "approved" (a weaker label);
   the training run can use both or only corrected ones.

Effort, to confirm on the first 20 invoices: if the model gets about 9 of 10 fields right, roughly 350
invoices need a few minutes each, so 2-3 person-days. Scanned invoices take longer.

**Boxes on scanned pages.** The prompt for image-only pages also asks for the position of each header value.
For training, the model's own boxes are kept on fields whose value is accepted or corrected to the same
position; no boxes are drawn by hand (see the fine-tuning doc).

| # | Item | Status |
|---|---|---|
| P6.1 | Comparison script (extraction vs keyed record → accept / review list) | ⬜ |
| P6.2 | Export: include source `kk-train`, a split field, keep evidence boxes for image-only pages | ⬜ |
| P6.3 | Review pass on the train split | ⏸ P4.3 |

### P7. Fine-tuning the extraction model (only if P5 shows reading errors)

**Decision rule.** Fine-tune only if, after the prompt fixes (P5.2), reading errors on the test split still
miss the gate (header fields ≥ 97 %) or invent values. Mapping, missing-data and keyed-differently misses are
not fixed by training and do not count.

**How.**

| Item | Choice |
|---|---|
| Base | Qwen2.5-VL-7B-Instruct, the model in production |
| Method | QLoRA (4-bit base, LoRA rank 16 on the language model's attention and MLP), vision encoder frozen |
| Input | Exactly what production sends: the v5/v6 system prompt, the same page images (`PAGE_RENDER_DPI` = 200, same size limit), the text layer when the route allows it |
| Target | The labelled JSON in the production schema, including the evidence boxes on image-only pages |
| Data | Train split from P6 (~350); dev split to pick the checkpoint |
| Runtime | Separate `train` container (PyTorch cu128 for the RTX 5090; transformers, peft, trl or Unsloth); vLLM stopped while training (one 24 GB GPU); checkpoints every 100-200 steps |
| Serving | vLLM with the LoRA adapter (`--enable-lora`, adapter registered as `qwen2.5-vl-7b-kk`); `EXTRACT_MODEL` points to it. Rollback: point it back to the base model |
| Checks before use | Test split (header gate, line F1, invented values, per supplier and held-out suppliers); synthetic `invoices-dev-55` regression; the choice nodes (the same served model answers them unless `CHOICE_MODEL` stays on the base) |

The held-out suppliers show whether the tuned model reads new layouts as well as the base model. If it does
worse on them, the adapter is not used for unknown suppliers.

| # | Item | Status |
|---|---|---|
| P7.1 | `train` container and `aap.finetune.train` (reads `extraction.jsonl`, writes the adapter) — built and smoke-tested on synthetic examples in advance | ⬜ |
| P7.2 | vLLM LoRA serving, `EXTRACT_MODEL` switch, rollback tested | ⬜ |
| P7.3 | Training run and evaluation on the real split | ⬜ decide after P5 |

### P8. GL coding from the real history

How it already works (`agents/nodes/gl.py`): for a non-PO line, the supplier's history with the same
description gives the account; otherwise the 5 most similar history lines (bge-m3) decide when they agree with
similarity ≥ `GL_SIM_AUTO` (0.90); otherwise the model picks among their accounts and the line goes to review.
When the store maps to a cost center, it replaces the cost-center segment, and the combination is checked.

With the real data:

- **History:** A3 + train split, minus test and dev, cut before the test period (P2).
- **Metrics on the test split**, against the keyed distributions:
  - account correct, weighted by amount (share of each invoice's amount coded to the keyed account);
  - natural account correct, and full combination correct, separately (the cost center may be right or wrong
    on its own);
  - share of lines coded without review; accuracy of those lines (must be high: they would post without a
    look);
  - per supplier.
- **Tuning on dev:** `GL_SIM_AUTO`, whether one supplier's history alone is enough or the same natural
  account across suppliers helps, the cost-center rule when no store is printed (supplier's most frequent
  cost center, or review).
- **Freight and tax lines:** their accounts per B7 (fixed account, the item's account, or EBS tax).
- **Lines vs distributions:** when AP keyed one line for a multi-line invoice, the GL result is compared at
  invoice level (amount per account), not line by line.

| # | Item | Status |
|---|---|---|
| P8.1 | GL metrics in the harness | ✅ `eval/gl.py`: amount-weighted accuracy (full combination and natural account), lines coded without review and their accuracy, gate `gl_no_review_accuracy` ≥ 98 % when GL is scored; from the Oracle-ready JSON after the agents (`--agents`) |
| P8.2 | Baseline GL result on test (part of P5) | ⏸ data |
| P8.3 | Threshold and cost-center rule tuning on dev | ⬜ |
| P8.4 | Freight / tax line accounts per B7 | ⏸ B7 |

### P9. Oracle-ready JSON, checked without EBS

**Content.** One JSON per invoice, built from the same payload the adapter writes to the interface, so the
JSON and what is posted cannot differ:

- header: invoice type code, invoice number, date, GL date, amount, currency, **supplier number and site code
  as well as their IDs**, OU, terms (name and ID), payment method, source, description;
- lines: line number, line type code, amount, description, quantity / unit price when printed, tax code,
  **account as the concatenated string** (and CCID);
- status: ready / needs review, with the reason for each field that is not certain;
- metadata: document ID, model and prompt version.

Supplier number, site code, terms name and the account string are in the JSON because IDs differ between the
Krispy Kreme instance and the internal one; whoever writes the push can use either.

**Checks, since there is no instance to import into:**

1. **Shadow comparison** on the test split: the JSON next to the keyed record, field by field, per invoice and
   in total. This shows whether the JSON is what AP would have keyed, and is the main evidence for Siva.
2. **Mock import on the replica:** the JSON's interface rows go through the mock Payables Open Interface
   Import on `ebs_kk`, which rejects an inactive supplier, a site that is not a pay site, an invalid or
   disabled account, a closed period or unknown terms with the real reject codes.
3. **JSON Schema** (`docs/oracle-invoice.schema.json`) and sample files, so Dhivya can test her push on her
   side whenever she has the code.

| # | Item | Status |
|---|---|---|
| P9.1 | Oracle-ready JSON from the interface payload; endpoint `GET /api/documents/{id}/oracle.json`; bulk export | ✅ `pipeline/oracle_json.py`; in every export ZIP |
| P9.2 | JSON Schema document and samples | ✅ `docs/ORACLE-JSON.md`, `docs/oracle-invoice.schema.json`, `docs/oracle-invoice.sample.json` |
| P9.3 | Shadow comparison report (test split) | ✅ built (`eval/shadow.py`, in every `--agents` run: equal / ready / ready-but-different, per field); run ⏸ P4.3 |
| P9.4 | Mock import of the test split on the replica | ⏸ P2.3 |

## 5. Code changes in one place

| Module | Change | Phase |
|---|---|---|
| `.gitignore`, `client-data/README.md` | Raw data outside git; handling rules | P1 |
| `src/aap/clientdata/` (new) | Readers, checker, manifest, comparison script | P1, P6 |
| `src/aap/config.py` | `GL_SEGMENT_COUNT`, `GL_NATURAL_ACCOUNT_SEGMENT`; `.env.kk` | P2 |
| `src/aap/ebs_mock/ddl/` | 8-segment index and KFV view; IBY payment methods | P2 |
| `src/aap/ebs_mock/client_load.py` (new) | Replica loader | P2 |
| `src/aap/adapters/ebs_mock.py`, `ebs_mock/import_program.py`, `ebs_mock/cli.py` | N segments, separator, natural-account segment | P2 |
| `src/aap/db/models.py` + migration | `RefCcid` segments; payment method on sites | P2 |
| `src/aap/mapping/map.py` | Terms fixes, lookup-driven invoice / line types, payment method | P3, P9 |
| `src/aap/agents/nodes/gl.py`, `agents/tools/gl.py` | Natural-account segment; cost-center rule without a store | P8 |
| `src/aap/clientdata/goldset.py` (new) | Gold set builder and split | P4 |
| `src/aap/eval/` | Partial truth, mapped-level fields, GL metrics, per-supplier report, shadow comparison | P4, P8, P9 |
| `src/aap/finetune/` | Export of `kk-train` with split and boxes; `train.py` (new) | P6, P7 |
| `docker-compose.yml` | `train` service (profile), vLLM LoRA flags | P7 |
| `src/aap/pipeline/export.py`, `api/main.py` | Oracle-ready JSON and endpoint | P9 |
| `docs/` | JSON Schema; this plan; PLAN-MID-DECEMBER status | P9 |

## 6. Order of work and what can start before the data

Everything marked "fixtures" is built on made-up files with the requested columns, so the real delivery only
has to be dropped in.

| Week | Before / without the data | With the data |
|---|---|---|
| Nov 3-7 | P1.1-P1.4 intake, checker, fixtures; P2.1-P2.2 8 segments, payment methods; P9.1-P9.2 Oracle-ready JSON; P3.2 terms fixes | Supplier list, B1-B7 arrive: run the checker, report gaps |
| Nov 10-14 | P2.3 loader on fixtures; P4.1-P4.2 gold set builder and harness; P8.1 GL metrics; P7.1 train container smoke test on synthetic data | A1-A3 arrive: checker, P2.3-P2.5 replica, P3.1/P3.4 sync and store table, P4.3 gold set and split |
| Nov 17-21 | P6.1 comparison script | P5.1 baseline; P5.2 prompt v6; P8.2-P8.3 GL; P6.3 review pass starts |
| Nov 24-28 | | P5.3 results and targets with Siva; P7.3 fine-tune if the decision rule says so |
| Dec 1-5 | | P9.3 shadow comparison, P9.4 mock import; re-run test with the final setup |
| Dec 8-12 | | Results per supplier, demo on held-out invoices, JSON samples handed over |

**If the data is late:** P1-P4 and P9.1-P9.2 do not depend on it. Each week of delay on A1/A2 removes a week
from P5-P7; fine-tuning (P7) is the first thing to drop, since the baseline, the GL result and the JSON are the
December deliverables.

**If the data is partial:** with B1-B7 and A3 but no attachments, the replica and GL coding can still be built
and the GL result measured on history alone (hold out the latest distributions and code them from their
descriptions). With attachments but no keyed record (A2), nothing can be measured; this is the one
combination to avoid.

## 7. What we show Siva in December

- Per supplier and in total, on invoices the system never saw: share of invoices whose JSON equals what AP keyed;
  header field accuracy; expense account accuracy (natural account and full combination); share that needed no
  review.
- The same for the held-out suppliers, as the evidence for "more suppliers is a matter of data".
- A live run: a held-out PDF uploaded → Oracle-ready JSON → the mock import on the replica creates the invoice.
- The JSON Schema and sample files for whoever writes the push.
- What changes on their side to go live: the push program (theirs), read access for the reference sync (§11).

## 8. Gates

| Gate | Split | Target |
|---|---|---|
| Header fields (mapped level: supplier, number, date, total, tax) | Test | ≥ 97 % |
| Lines, where keyed lines match printed ones | Test | F1 ≥ 95 % |
| Invented header values | Test | 0 |
| Expense account, amount-weighted | Test | Agreed with Siva after P5 |
| Lines coded without review: accuracy | Test | ≥ 98 % (otherwise raise `GL_SIM_AUTO`) |
| Synthetic regression `invoices-dev-55` | – | Both gates after every prompt or model change |
| Mock import | Test | Every invoice marked ready is accepted by the replica's import |

## 9. Handling the client data

- Raw files only under `client-data/`, gold sets only under `test-data-kk/`; both git-ignored; on the project
  machine only, not on shared drives or in chat tools.
- `ebs_kk` / `aap_kk` and the file store of the replica hold client data too: the same rule; dropped at the end
  of the project or when the client asks.
- No client data in commits, docs, test fixtures, screenshots in docs, or the Claude Docs. Fixtures are made up.
- Training data and adapters trained on it are client data as well.
- Written approval to store and train on the invoices before P4.3 (J-1).

## 10. Open questions

| Question | For | Blocks |
|---|---|---|
| Which values the JSON and the replica carry: does the internal instance recreate KK's values (supplier numbers, site codes, terms, 8 segments) or re-map them? Vikram's 2026-11-04 sheet is the Vision demo setup, not KK's (B-0) | Vikram, Siva | P2.3, P9 target values |
| KK IDs or internal IDs in the extracts? Joins across instances use business keys either way | Vikram | P2.3 |
| Which segment is the natural account, which the cost center? | Vikram | P2.1 settings |
| Tax as lines, EBS tax or header amount; freight account | Vikram | P8.4, P9.1 |
| Payment method per invoice or site default | Vikram | P9.1 |
| Invoice types in scope (credit memos?) | Dhivya | P3.3 |
| Interface columns or an API payload for the JSON? | Siva / Dhivya | P9.1 field names |
| Invoice source: the import needs our `SOURCE` code (`INVOICE_SOURCE`, default `AAP`) in the instance's SOURCE lookup. Create `AAP` there, or use an existing source? V-LOV fails every invoice until the list has it | Vikram | V-LOV on the replica, the import |
| GL accuracy target | Siva | P5.3 |

## 11. Later: connecting to a real EBS

Not part of December. When read access exists (internal instance or Krispy Kreme):

- a python-oracledb adapter implementing `ErpReadPort` (the mock's SQL was written to carry over);
- `refsync` against it instead of `ebs_kk`; the replica loader is no longer needed;
- the push stays on their side, using the Oracle-ready JSON, or our write adapter if they want it.

Nothing else in the pipeline changes.
