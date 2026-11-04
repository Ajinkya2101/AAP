"""Client replica of the mock EBS: the same schema as the synthetic mock, filled from client extracts
instead of generated data (IMPLEMENTATION-PLAN-CLIENT-DATA P2).

    python -m aap.clientdata.replica ...      (creates the databases, loads this, syncs aap_kk)

Kept from the synthetic seed, because they are not client-specific: currencies, lookups, users,
concurrent programs, the AP tolerance templates, tax rates, units of measure, daily exchange rates.
Everything else comes from the deliveries, when they have it:

| Extract                        | Mock tables                                                          |
|--------------------------------|----------------------------------------------------------------------|
| HR_OPERATING_UNITS             | hr_all_organization_units, hr_organization_information, gl_ledgers   |
| FND_ID_FLEX_SEGMENTS, _VALUES  | fnd_id_flex_structures / _segments, fnd_flex_value_sets / _values(_tl)|
| GL_CODE_COMBINATIONS           | gl_code_combinations                                                 |
| AP_TERMS (+ AP_TERMS_LINES)    | ap_terms_tl, ap_terms_lines (lines from the names when not delivered)|
| IBY_PAYMENT_METHODS            | iby_payment_methods_b / _tl                                          |
| AP_SUPPLIERS, _SITES_ALL       | hz_parties, ap_suppliers, ap_supplier_sites_all                      |
| AP_INVOICES_ALL, _LINES, _DIST | ap_invoices_all, ap_invoice_lines_all, ap_invoice_distributions_all  |

GL periods are generated for every ledger, open from the earliest history month to three months
ahead. Invoices listed in a gold set's history_exclude.json (test and dev split), every invoice of
its held-out suppliers and everything on or after its cut-off are left out of the history, so GL
coding is never measured on its own answer. IDs are kept as delivered (one ID space per delivery).
"""

from __future__ import annotations

import calendar
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg

from aap.clientdata.readers import Delivery, TableData, day, dec, num, read_delivery
from aap.clientdata.terms import payment_method_rows, term_rows
from aap.common.coa import CoaLayout

from .ddl_loader import apply_ddl
from .seed import foundation, gl, items, terms_tax
from .seed.context import SCALES, TABLE_ORDER, SeedContext
from .seed.invariants import check

START = datetime(2015, 1, 1)
BUSINESS_GROUP_ID = 1
DEFAULT_COA_ID = 101
# Foundation tables taken from the synthetic seed as they are.
FOUNDATION_TABLES = ["fnd.fnd_currencies", "fnd.fnd_lookup_values", "fnd.fnd_user", "fnd.fnd_concurrent_programs",
                     "gl.gl_daily_rates", "ap.ap_tolerance_templates", "zx.zx_rates_b", "inv.mtl_units_of_measure_tl"]
# Seed checks that only make sense for the synthetic fixtures.
SYNTHETIC_ONLY_CHECKS = ("S1 fixture",)
# Sequences set past the loaded IDs, so rows created later (interface import) do not collide.
SEQUENCES = {"ap.ap_invoices_s": ("ap.ap_invoices_all", "invoice_id"),
             "ap.ap_invoice_distributions_s": ("ap.ap_invoice_distributions_all", "invoice_distribution_id"),
             "ap.ap_suppliers_s": ("ap.ap_suppliers", "vendor_id"),
             "ap.ap_supplier_sites_s": ("ap.ap_supplier_sites_all", "vendor_site_id"),
             "ap.ap_terms_s": ("ap.ap_terms_tl", "term_id"),
             "gl.gl_code_combinations_s": ("gl.gl_code_combinations", "code_combination_id"),
             "hz.hz_parties_s": ("hz.hz_parties", "party_id"),
             "fnd.fnd_flex_value_sets_s": ("fnd.fnd_flex_value_sets", "flex_value_set_id"),
             "fnd.fnd_flex_values_s": ("fnd.fnd_flex_values", "flex_value_id")}
EXTRA_TABLES = ["fnd.fnd_id_flex_structures", "fnd.fnd_id_flex_segments", "iby.iby_payment_methods_b",
                "iby.iby_payment_methods_tl"]


@dataclass
class Replica:
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=lambda: defaultdict(list))
    notes: list[str] = field(default_factory=list)

    def add(self, table: str, **row: Any) -> None:
        self.rows[table].append(row)


# ------------------------------------------------------------------ input
def merge_deliveries(paths: list[Path]) -> Delivery:
    """Several deliveries read as one; a later delivery's row replaces an earlier one with the same key."""
    merged = Delivery(root=paths[-1])
    for path in paths:
        d = read_delivery(path)
        for name, t in d.tables.items():
            have = merged.tables.get(name)
            if have is None:
                merged.tables[name] = TableData(spec=t.spec, rows=list(t.rows), files=list(t.files),
                                                columns=list(t.columns))
                continue
            by_key = {tuple(r.get(k) for k in t.spec.key): r for r in have.rows}
            by_key.update({tuple(r.get(k) for k in t.spec.key): r for r in t.rows})
            have.rows, have.files = list(by_key.values()), have.files + t.files
            have.columns = list(dict.fromkeys([*have.columns, *t.columns]))
        merged.attachments += d.attachments
        merged.files.update({f"{path.name}/{k}": v for k, v in d.files.items()})
    return merged


def read_exclusions(path: Path | None) -> tuple[set[int], set[int], date | None]:
    """(invoice IDs, held-out supplier numbers as text, cut-off date) from a gold set's history_exclude.json."""
    if path is None:
        return set(), set(), None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cutoff = date.fromisoformat(data["history_cutoff"]) if data.get("history_cutoff") else None
    return set(data.get("invoice_ids") or []), set(data.get("holdout_suppliers") or []), cutoff  # type: ignore[arg-type]


# --------------------------------------------------------------- building
def _foundation(r: Replica) -> None:
    ctx = SeedContext(seed=42, scale=SCALES["small"], anchor=date.today())
    foundation.generate(ctx)
    terms_tax.generate(ctx)
    gl._rates(ctx)
    for uom, code, cls in items.UOMS:
        ctx.add("inv.mtl_units_of_measure_tl", unit_of_measure=uom, uom_code=code, uom_class=cls,
                base_uom_flag="Y" if uom in ("Each", "Hour", "Kilogram", "Liter", "Meter") else "N",
                unit_of_measure_tl=uom, description=uom, language="US", source_lang="US")
    for table in FOUNDATION_TABLES:
        r.rows[table] = list(ctx.rows.get(table) or [])


def _coa_id(d: Delivery) -> int:
    for table, col in (("GL_CODE_COMBINATIONS", "CHART_OF_ACCOUNTS_ID"), ("FND_ID_FLEX_SEGMENTS", "ID_FLEX_NUM")):
        ids = {num(x.get(col)) for x in d.rows(table)} - {None}
        if ids:
            return min(ids)  # type: ignore[type-var]
    return DEFAULT_COA_ID


def _chart(r: Replica, d: Delivery, coa: CoaLayout, coa_id: int) -> None:
    r.add("fnd.fnd_id_flex_structures", application_id=101, id_flex_code="GL#", id_flex_num=coa_id,
          id_flex_structure_code="CLIENT_ACCOUNTING_FLEX", id_flex_structure_name="Client Accounting Flex",
          concatenated_segment_delimiter=coa.separator, enabled_flag="Y")
    value_sets: dict[str, int] = {}
    vs_id, fv_id = 2000000, 3000000
    for v in sorted(d.rows("FND_FLEX_VALUES"), key=lambda x: (str(x["FLEX_VALUE_SET_NAME"]), str(x["FLEX_VALUE"]))):
        name = str(v["FLEX_VALUE_SET_NAME"]).upper()
        if name not in value_sets:
            vs_id += 1
            value_sets[name] = vs_id
            r.add("fnd.fnd_flex_value_sets", flex_value_set_id=vs_id, flex_value_set_name=name, validation_type="I",
                  format_type="C", maximum_size=25, description=name)
        fv_id += 1
        r.add("fnd.fnd_flex_values", flex_value_id=fv_id, flex_value_set_id=value_sets[name],
              flex_value=str(v["FLEX_VALUE"]), enabled_flag=v.get("ENABLED_FLAG") or "Y",
              summary_flag=v.get("SUMMARY_FLAG") or "N", end_date_active=day(v.get("END_DATE_ACTIVE")))
        r.add("fnd.fnd_flex_values_tl", flex_value_id=fv_id, language="US", source_lang="US",
              description=v.get("DESCRIPTION"), flex_value_meaning=str(v["FLEX_VALUE"]))
    delivered = {str(s["APPLICATION_COLUMN_NAME"]).upper(): s for s in d.rows("FND_ID_FLEX_SEGMENTS")}
    for n in range(1, coa.segment_count + 1):
        s = delivered.get(f"SEGMENT{n}", {})
        vset = str(s.get("FLEX_VALUE_SET_NAME") or "").upper()
        r.add("fnd.fnd_id_flex_segments", application_id=101, id_flex_code="GL#", id_flex_num=coa_id,
              application_column_name=f"SEGMENT{n}", segment_name=s.get("SEGMENT_NAME") or f"Segment {n}",
              segment_num=num(s.get("SEGMENT_NUM")) or n, flex_value_set_id=value_sets.get(vset),
              enabled_flag="Y", display_flag="Y")
    if d.has("GL_CODE_COMBINATIONS") and not d.has("FND_ID_FLEX_SEGMENTS"):
        r.notes.append("segment meanings not delivered: segments named 'Segment N', no descriptions (B-2)")
    for c in d.rows("GL_CODE_COMBINATIONS"):
        segs = {f"segment{i}": (str(c[f"SEGMENT{i}"]) if c.get(f"SEGMENT{i}") is not None else None)
                for i in range(1, 31)}
        r.add("gl.gl_code_combinations", code_combination_id=num(c["CODE_COMBINATION_ID"]), chart_of_accounts_id=coa_id,
              account_type=c.get("ACCOUNT_TYPE") or "E", enabled_flag=c.get("ENABLED_FLAG") or "Y",
              summary_flag=c.get("SUMMARY_FLAG") or "N",
              detail_posting_allowed_flag=c.get("DETAIL_POSTING_ALLOWED_FLAG") or "Y",
              start_date_active=day(c.get("START_DATE_ACTIVE")), end_date_active=day(c.get("END_DATE_ACTIVE")),
              **segs)


def _org(r: Replica, d: Delivery, coa_id: int) -> dict[int, int]:
    """Operating units and their ledgers; returns org_id -> ledger_id. OUs used by sites or invoices
    but not delivered get a placeholder (named "OU <id>") so the rows can be loaded."""
    ous = {num(x["ORGANIZATION_ID"]): x for x in d.rows("HR_OPERATING_UNITS")}
    used = {num(x.get("ORG_ID")) for t in ("AP_SUPPLIER_SITES_ALL", "AP_INVOICES_ALL") for x in d.rows(t)} - {None}
    missing = sorted(used - set(ous))  # type: ignore[type-var]
    if missing:
        r.notes.append(f"operating units used but not delivered (placeholders created): {missing}")
    currencies: dict[int | None, list[str]] = defaultdict(list)
    for x in d.rows("AP_INVOICES_ALL"):
        if x.get("INVOICE_CURRENCY_CODE"):
            currencies[num(x.get("ORG_ID"))].append(str(x["INVOICE_CURRENCY_CODE"]))
    if not ous and not missing:
        return {}
    r.add("hr.hr_all_organization_units", organization_id=BUSINESS_GROUP_ID, business_group_id=BUSINESS_GROUP_ID,
          name="Client Business Group", type="BG", date_from=START)
    ledgers: dict[int, int] = {}
    info_id = 900000
    for org_id in sorted({*ous, *missing}):  # type: ignore[type-var]
        x = ous.get(org_id, {})
        ledger_id = num(x.get("SET_OF_BOOKS_ID")) or 900000 + org_id
        cur = x.get("CURRENCY_CODE") or (max(set(currencies[org_id]), key=currencies[org_id].count)
                                         if currencies[org_id] else "USD")
        if ledger_id not in ledgers.values():
            r.add("gl.gl_ledgers", ledger_id=ledger_id, name=f"{(x.get('NAME') or f'OU {org_id}')[:22]} Ledger",
                  short_name=f"L{ledger_id}"[:20], chart_of_accounts_id=coa_id, currency_code=cur,
                  period_set_name="Client Calendar", accounted_period_type="Month")
        ledgers[org_id] = ledger_id
        r.add("hr.hr_all_organization_units", organization_id=org_id, business_group_id=BUSINESS_GROUP_ID,
              name=x.get("NAME") or f"OU {org_id}", type="OU", date_from=START, date_to=day(x.get("DATE_TO")))
        for context, values in (("CLASS", dict(org_information1="OPERATING_UNIT", org_information2="Y")),
                                ("Operating Unit Information",
                                 dict(org_information3=str(ledger_id), org_information5=x.get("SHORT_CODE")))):
            info_id += 1
            r.add("hr.hr_organization_information", org_information_id=info_id, organization_id=org_id,
                  org_information_context=context, **values)
    return ledgers


def _periods(r: Replica, ledgers: dict[int, int], first: date | None) -> None:
    start = (first or date.today() - timedelta(days=730)).replace(day=1)
    end = (date.today().replace(day=1) + timedelta(days=95)).replace(day=1)
    for ledger_id in sorted(set(ledgers.values())):
        d = start
        while d <= end:
            last = calendar.monthrange(d.year, d.month)[1]
            for app_id in (200, 101):
                r.add("gl.gl_period_statuses", application_id=app_id, ledger_id=ledger_id, set_of_books_id=ledger_id,
                      period_name=gl.period_name(d), closing_status="O", start_date=datetime(d.year, d.month, 1),
                      end_date=datetime(d.year, d.month, last), period_type="Month", period_year=d.year,
                      period_num=d.month, quarter_num=(d.month - 1) // 3 + 1, adjustment_period_flag="N",
                      effective_period_num=d.year * 10000 + d.month)
            d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)


def _suppliers(r: Replica, d: Delivery) -> None:
    party_id = 5000000
    for s in d.rows("AP_SUPPLIERS"):
        pid = num(s.get("PARTY_ID"))
        if pid is None:
            party_id += 1
            pid = party_id
        r.add("hz.hz_parties", party_id=pid, party_number=str(pid), party_name=s["VENDOR_NAME"],
              party_type="ORGANIZATION", jgzz_fiscal_code=s.get("VAT_REGISTRATION_NUM") or s.get("NUM_1099"))
        r.add("ap.ap_suppliers", vendor_id=num(s["VENDOR_ID"]), vendor_name=s["VENDOR_NAME"],
              vendor_name_alt=s.get("VENDOR_NAME_ALT"), segment1=str(s["SEGMENT1"]), party_id=pid,
              vendor_type_lookup_code=s.get("VENDOR_TYPE_LOOKUP_CODE") or "VENDOR",
              enabled_flag=s.get("ENABLED_FLAG") or "Y", num_1099=s.get("NUM_1099"),
              vat_registration_num=s.get("VAT_REGISTRATION_NUM"), terms_id=num(s.get("TERMS_ID")),
              pay_group_lookup_code=s.get("PAY_GROUP_LOOKUP_CODE"),
              invoice_currency_code=s.get("INVOICE_CURRENCY_CODE"),
              payment_method_lookup_code=s.get("PAYMENT_METHOD_LOOKUP_CODE"),
              end_date_active=day(s.get("END_DATE_ACTIVE")), attribute1="N")    # DFF1 PO required: non-PO scope
    for s in d.rows("AP_SUPPLIER_SITES_ALL"):
        r.add("ap.ap_supplier_sites_all", vendor_site_id=num(s["VENDOR_SITE_ID"]), vendor_id=num(s["VENDOR_ID"]),
              vendor_site_code=str(s["VENDOR_SITE_CODE"])[:15], org_id=num(s["ORG_ID"]),
              purchasing_site_flag=s.get("PURCHASING_SITE_FLAG") or "Y", pay_site_flag=s.get("PAY_SITE_FLAG") or "Y",
              primary_pay_site_flag=s.get("PRIMARY_PAY_SITE_FLAG") or "N", address_line1=s.get("ADDRESS_LINE1"),
              city=s.get("CITY"), state=s.get("STATE"), zip=s.get("ZIP"), country=s.get("COUNTRY"),
              email_address=s.get("EMAIL_ADDRESS"), terms_id=num(s.get("TERMS_ID")),
              invoice_currency_code=s.get("INVOICE_CURRENCY_CODE"),
              payment_currency_code=s.get("PAYMENT_CURRENCY_CODE"),
              pay_group_lookup_code=s.get("PAY_GROUP_LOOKUP_CODE"),
              payment_method_lookup_code=s.get("PAYMENT_METHOD_LOOKUP_CODE"),
              terms_date_basis=s.get("TERMS_DATE_BASIS"),
              accts_pay_code_combination_id=num(s.get("ACCTS_PAY_CODE_COMBINATION_ID")),
              inactive_date=day(s.get("INACTIVE_DATE")))


def _history(r: Replica, d: Delivery, ledgers: dict[int, int],
             exclude: tuple[set[int], set[int], date | None]) -> date | None:
    """Invoices, lines and distributions, without the gold set's test / dev invoices. Returns the
    earliest invoice date loaded (for the GL periods)."""
    ids, holdout, cutoff = exclude
    numbers = {num(s.get("VENDOR_ID")): str(s.get("SEGMENT1")) for s in d.rows("AP_SUPPLIERS")}
    kept: dict[int, dict[str, Any]] = {}
    dropped = 0
    for x in d.rows("AP_INVOICES_ALL"):
        inv_id, inv_date = num(x["INVOICE_ID"]), day(x.get("INVOICE_DATE"))
        if inv_id in ids or numbers.get(num(x.get("VENDOR_ID"))) in holdout or (cutoff and inv_date and inv_date >= cutoff):
            dropped += 1
            continue
        kept[inv_id] = x  # type: ignore[index]
    if dropped:
        r.notes.append(f"{dropped} invoice(s) left out of the history (gold set test / dev, held-out suppliers, cut-off)")
    first = None
    gl_dates: dict[int, date] = {}
    for inv_id, x in kept.items():
        org = num(x.get("ORG_ID"))
        inv_date = day(x.get("INVOICE_DATE")) or date.today()
        gl_date = day(x.get("GL_DATE")) or inv_date
        gl_dates[inv_id] = gl_date
        first = min(first or inv_date, inv_date, gl_date)
        currency = x.get("INVOICE_CURRENCY_CODE") or "USD"
        r.add("ap.ap_invoices_all", invoice_id=inv_id, invoice_num=str(x["INVOICE_NUM"]), vendor_id=num(x["VENDOR_ID"]),
              vendor_site_id=num(x.get("VENDOR_SITE_ID")), org_id=org, set_of_books_id=ledgers.get(org),  # type: ignore[arg-type]
              invoice_type_lookup_code=x.get("INVOICE_TYPE_LOOKUP_CODE") or "STANDARD", invoice_date=inv_date,
              gl_date=gl_date, invoice_amount=dec(x.get("INVOICE_AMOUNT")), invoice_currency_code=currency,
              payment_currency_code=x.get("PAYMENT_CURRENCY_CODE") or currency, terms_id=num(x.get("TERMS_ID")),
              terms_date=day(x.get("TERMS_DATE")), description=x.get("DESCRIPTION"),
              source=x.get("SOURCE") or "MANUAL INVOICE ENTRY", payment_method_code=x.get("PAYMENT_METHOD_CODE"),
              pay_group_lookup_code=x.get("PAY_GROUP_LOOKUP_CODE"), cancelled_date=day(x.get("CANCELLED_DATE")),
              total_tax_amount=dec(x.get("TOTAL_TAX_AMOUNT")), approval_status="APPROVED",
              wfapproval_status="NOT REQUIRED", payment_status_flag="Y")
    line_types: dict[tuple, str] = {}
    for x in d.rows("AP_INVOICE_LINES_ALL"):
        inv_id = num(x["INVOICE_ID"])
        if inv_id not in kept:
            continue
        org = num(kept[inv_id].get("ORG_ID"))  # type: ignore[index]
        key = (inv_id, num(x["LINE_NUMBER"]))
        line_types[key] = x["LINE_TYPE_LOOKUP_CODE"]
        r.add("ap.ap_invoice_lines_all", invoice_id=inv_id, line_number=key[1],
              line_type_lookup_code=x["LINE_TYPE_LOOKUP_CODE"], org_id=org, description=x.get("DESCRIPTION"),
              amount=dec(x.get("AMOUNT")), quantity_invoiced=dec(x.get("QUANTITY_INVOICED")),
              unit_price=dec(x.get("UNIT_PRICE")),
              accounting_date=day(x.get("ACCOUNTING_DATE")) or gl_dates[inv_id],  # type: ignore[index]
              cancelled_flag=x.get("CANCELLED_FLAG") or "N", set_of_books_id=ledgers.get(org))  # type: ignore[arg-type]
    seq: dict[tuple, int] = defaultdict(int)
    for x in sorted(d.rows("AP_INVOICE_DISTRIBUTIONS_ALL"), key=lambda y: num(y["INVOICE_DISTRIBUTION_ID"]) or 0):
        inv_id = num(x["INVOICE_ID"])
        key = (inv_id, num(x["INVOICE_LINE_NUMBER"]))
        if inv_id not in kept or key not in line_types:
            continue
        seq[key] += 1
        org = num(kept[inv_id].get("ORG_ID"))  # type: ignore[index]
        r.add("ap.ap_invoice_distributions_all", invoice_distribution_id=num(x["INVOICE_DISTRIBUTION_ID"]),
              invoice_id=inv_id, invoice_line_number=key[1],
              distribution_line_number=num(x.get("DISTRIBUTION_LINE_NUMBER")) or seq[key],
              line_type_lookup_code=x.get("LINE_TYPE_LOOKUP_CODE") or line_types[key], amount=dec(x.get("AMOUNT")),
              description=x.get("DESCRIPTION"),
              accounting_date=day(x.get("ACCOUNTING_DATE")) or gl_dates[inv_id],  # type: ignore[index]
              dist_code_combination_id=num(x["DIST_CODE_COMBINATION_ID"]), org_id=org,
              set_of_books_id=ledgers.get(org), reversal_flag=x.get("REVERSAL_FLAG"),  # type: ignore[arg-type]
              parent_reversal_id=num(x.get("PARENT_REVERSAL_ID")), match_status_flag="A", posted_flag="Y")
    return first


def lookup_rows(r: Replica, d: Delivery) -> list[str]:
    """The delivered lookup types replace the seeded rows of the same type (the instance's own list
    of invoice types, line types, sources); the other seeded lookups stay. Returns notes."""
    delivered: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for x in d.rows("FND_LOOKUP_VALUES"):
        delivered[str(x["LOOKUP_TYPE"]).strip().upper()].append(x)
    kept = [x for x in r.rows["fnd.fnd_lookup_values"] if x["lookup_type"] not in delivered]
    for lookup_type, rows in sorted(delivered.items()):
        for x in rows:
            code = str(x["LOOKUP_CODE"]).strip()
            kept.append(dict(lookup_type=lookup_type, language="US", lookup_code=code,
                             meaning=str(x.get("MEANING") or code)[:80], description=x.get("DESCRIPTION"),
                             enabled_flag=str(x.get("ENABLED_FLAG") or "Y")[:1].upper(),
                             start_date_active=day(x.get("START_DATE_ACTIVE")) or START,
                             end_date_active=day(x.get("END_DATE_ACTIVE")), source_lang="US",
                             security_group_id=0, view_application_id=200))
    r.rows["fnd.fnd_lookup_values"] = kept
    return ["lookups from the delivery: " + ", ".join(f"{t} ({len(v)})" for t, v in sorted(delivered.items()))]


def build_replica(d: Delivery, coa: CoaLayout, *, exclude: Path | None = None) -> Replica:
    """Every row of the replica, in memory (no database)."""
    r = Replica()
    _foundation(r)
    coa_id = _coa_id(d)
    _chart(r, d, coa, coa_id)
    terms, notes = term_rows(d)
    for table, rows in terms.items():
        r.rows[table] = rows
    derived = [n for n in notes if "derived" in n]
    unknown = [n.split(":")[0] for n in notes if "unknown" in n]
    if derived:
        r.notes.append(f"{len(derived)} term(s) with lines derived from the name (AP_TERMS_LINES not delivered)")
    if unknown:
        r.notes.append(f"terms without days (no lines, name not parseable): {unknown}")
    if d.has("FND_LOOKUP_VALUES"):
        r.notes += lookup_rows(r, d)
    if d.has("IBY_PAYMENT_METHODS"):
        r.rows.update(payment_method_rows(d))
    else:
        r.notes.append("IBY_PAYMENT_METHODS not delivered: no payment methods")
    ledgers = _org(r, d, coa_id)
    _suppliers(r, d)
    first = _history(r, d, ledgers, read_exclusions(exclude))
    _periods(r, ledgers, first)
    if not d.has("AP_SUPPLIERS"):
        r.notes.append("no suppliers delivered (B-1): the replica cannot map invoices yet")
    return r


# ---------------------------------------------------------------- loading
def _columns(rows: list[dict[str, Any]]) -> list[str]:
    return list(dict.fromkeys(k for row in rows for k in row))


def load_replica(dsn: str, r: Replica) -> dict[str, Any]:
    """Recreate the mock EBS schema in `dsn` and load the rows. Runs in one transaction."""
    order = TABLE_ORDER[:]
    for t in EXTRA_TABLES:
        if t not in order:
            order.insert(order.index("hr.hr_locations_all"), t)
    counts: dict[str, int] = {}
    with psycopg.connect(dsn) as conn:
        apply_ddl(conn, drop_existing=True)
        with conn.cursor() as cur:
            for table in order:
                rows = r.rows.get(table) or []
                if not rows:
                    continue
                cols = _columns(rows)
                with cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:  # type: ignore[arg-type]
                    for row in rows:
                        copy.write_row([row.get(c) for c in cols])
                counts[table] = len(rows)
            for seq, (table, col) in SEQUENCES.items():
                cur.execute(f"SELECT setval('{seq}', greatest((SELECT coalesce(max({col}), 0) FROM {table}), "  # noqa: S608
                            f"(SELECT last_value FROM {seq})))")
        failures = {k: v for k, v in check(conn).items() if not k.startswith(SYNTHETIC_ONLY_CHECKS)}
        conn.commit()
    return {"counts": counts, "invariant_failures": failures, "notes": r.notes}

