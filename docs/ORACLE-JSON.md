# Oracle-ready JSON

One JSON document per invoice, with everything Oracle EBS needs to create it. This is the output Siva asked
for on 2026-11-02: invoice in, one JSON out. The program that creates the invoice in Oracle, through the
Payables Open Interface or an API, belongs to the EBS team and reads this file.

| | |
|---|---|
| Schema | [oracle-invoice.schema.json](oracle-invoice.schema.json) (JSON Schema 2020-12), `schema_id` `aap.oracle-invoice`, version 1.0 |
| Sample | [oracle-invoice.sample.json](oracle-invoice.sample.json) (made-up values) |
| One invoice | `GET /api/documents/{id}/oracle.json` |
| Schema over HTTP | `GET /api/schemas/oracle-invoice.json` |
| In the export ZIPs | `oracle_invoice.json` in every document folder of `…/export.zip` and `/api/export.zip` |
| Code | `src/aap/pipeline/oracle_json.py`, built from `src/aap/adapters/payload.py` |

## How to read it

```
meta        which document, version, model and prompt; the account layout; whose values it carries
status      can it be created as it is? state, postable, missing fields, uncertain values with reasons
header      AP_INVOICES_INTERFACE columns, by business key
lines[]     AP_INVOICE_LINES_INTERFACE columns, each with its distributions[]
source_ids  IDs in the instance the reference data came from (valid only there)
```

**Create the invoice only when `status.postable` is true.** That means the state is `ready` (nothing for a
clerk to decide) or `approved` (a clerk approved it), and every required field has a value. Otherwise:

- `status.missing` lists the required fields that are empty, as JSON paths (`header.payment_method_code`,
  `lines[2].dist_code_concatenated`).
- `status.issues` lists the values that are filled but not certain, each with a code and a reason. Examples:
  `check_map` (the value was matched but should be checked), `gl_review` (the GL account was chosen by the
  model), `chosen_by_model`, `not_found_in_source`, and `not_in_instance_map` (see below).
- `status.review_reasons` and `status.validation_messages` are what the review screen shows.

| `status.state` | Meaning |
|---|---|
| `ready` | In review with nothing open: no review reason, no missing field, no issue, validation not failed |
| `needs_review` / `on_hold` | A clerk has to decide first |
| `approved` | A clerk approved this version (`meta.invoice_version_kind` = `approved`) |
| `submitted` / `created` | Already sent to the mock Oracle by the application |
| `rejected`, `not_ready` | Rejected, or still being read |

Before approval, the JSON is the newest reviewed version with the agents' PO and GL decisions
(`meta.invoice_version_kind` = `proposed`). That is exactly what approval would freeze.

## Business keys, not IDs

Supplier and site IDs, terms IDs and CCIDs differ between the Krispy Kreme instance and the internal one.
So `header` carries only values that mean the same thing on every instance:

| Workbench field (Vikram's mandatory list) | JSON |
|---|---|
| Operating Unit | `header.operating_unit` (name) |
| Type | `header.invoice_type_lookup_code` |
| Trading Partner / Supplier Num / Supplier Site | `header.vendor_name`, `header.vendor_num`, `header.vendor_site_code` |
| Invoice Date / Num / Curr / Amount | `header.invoice_date`, `header.invoice_num`, `header.invoice_currency_code`, `header.invoice_amount` |
| GL Date | `header.gl_date`, and `accounting_date` on every line and distribution |
| Payment Curr / Terms Date / Terms / Payment Method | `header.payment_currency_code`, `header.terms_date`, `header.terms_name`, `header.payment_method_code` |
| Line Num / Type / Amount | `lines[].line_number`, `line_type_lookup_code`, `amount` |
| Distribution Num / Type / Amount / GL Date / Account | `lines[].distributions[]`; the account is the concatenated segments |

The IDs of the source instance are in `source_ids`. They are usable only on that instance.

Amounts are strings with a decimal point (`"1274.95"`) so no precision is lost. Dates are `YYYY-MM-DD`.
`meta.account_layout` gives the segment count and separator of the account strings.

## Lines and distributions

| `account_source` | Line | Distributions |
|---|---|---|
| `line` | A non-PO item, or a freight / miscellaneous line with its own account | One, for the whole line amount, to `dist_code_concatenated` |
| `po_match` | Matched to a PO (`po_number`, `po_line_number`, `release_num`) | None: Oracle takes the PO distribution's account |
| `oracle_setup` | Tax or freight with no account of its own | None: Oracle's tax or freight setup decides |

Header-level tax and freight become their own lines, so `invoice_amount` equals the sum of the lines.

## Another instance's values

If the target instance is not set up with the same supplier numbers, site codes, term names, payment
methods and account segments, the differences go into a CSV named by the `INSTANCE_MAP_FILE` setting
(`kind,scope,source,target`; the format is in `src/aap/mapping/instance.py`). The JSON is then translated
when it is built, and `meta.target_instance` names the map.

Kinds with no rows in the map are taken to be the same in both instances. Within a kind that has rows,
a value without a row is passed on unchanged and reported as `not_in_instance_map`. Without a map, the
JSON carries the source instance's values.

## Still open

- **Payment method:** taken from the supplier site default, or the supplier's. Whether AP ever keys a
  printed method instead is question B-6.
- **Tax and freight accounts:** set by Oracle's setup until B-7 is answered.
- **Terms date:** follows the site's `TERMS_DATE_BASIS`, or the `TERMS_DATE_BASIS` setting (default
  Invoice) when the site has none.
- **Interface or API:** the field names follow the Payables Open Interface. If the push uses an API, the
  mapping stays one to one (B-8).
