# Client data

Raw deliveries from the client (Krispy Kreme extracts and invoice attachments). Everything in this
folder except this README is git-ignored. Rules: IMPLEMENTATION-PLAN-CLIENT-DATA.md section 9.

## Layout

```
client-data/kk/<delivery date>/     one folder per delivery, kept exactly as received
    *.csv | *.xlsx                  EBS table extracts, one table per file or per sheet
    attachments/<INVOICE_ID>.pdf    invoice files named by their INVOICE_ID (4783348.pdf, 4783348_2.pdf)
    manifest.json                   written by the checker: files, SHA-256, rows per table, result
    check-report.md                 written by the checker: what does not join or add up
```

File and sheet names are matched to tables by name (`src/aap/clientdata/tables.py`): the EBS table name
(`ap_invoices_all.csv`) or a known alias (`pay_terms.xlsx` = AP_TERMS, `pay_method.xlsx` =
IBY_PAYMENT_METHODS). Oracle SQL Developer exports ("Export Worksheet" + "SQL" sheets) are read as they are.

## Commands

```bat
:: what a delivery should look like (made-up data)
uv run python -m aap.clientdata.fixtures client-data\example

:: check a delivery before anything is loaded; exit code 1 on errors
set GL_SEGMENT_COUNT=8& set GL_SEGMENT_SEPARATOR=.& set GL_COST_CENTER_SEGMENT=3& set GL_NATURAL_ACCOUNT_SEGMENT=4
uv run python -m aap.clientdata.check client-data\kk\2026-11-10

:: pair attachments with what AP keyed; test / dev / train split (output in test-data-kk\, also git-ignored)
uv run python -m aap.clientdata.goldset client-data\kk\2026-11-10 --holdout-count 2

:: (re)load the replica databases ebs_kk / aap_kk from every delivery so far (the synthetic ebs / aap are
:: not touched); settings from .env.kk (copy .env.kk.example); --local = run from the host
uv run python -m aap.clientdata.replica --local --create-databases ^
    --from client-data\kk\2026-11-02-dhivya --from client-data\kk\2026-11-10 ^
    --exclude test-data-kk\2026-11-10\history_exclude.json
```

Deliveries so far: `kk/2026-11-02-dhivya/` (pay_terms.xlsx, pay_method.xlsx).

## Handling

- Only on the project machine: not on shared drives, not in chat tools, not in commits, docs or screenshots.
- Test fixtures are made up (`aap.clientdata.fixtures`); never copy client values into `tests/` or `test-data/`.
- The replica databases and gold sets built from this data are client data too.
- Storing and training on the invoices needs the written approval (J-1, PLAN-MID-DECEMBER).
- Delete at the end of the project or when the client asks.
