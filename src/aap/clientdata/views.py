"""Turn extracts that are not one EBS table each into the tables the checks and loaders read.

Run by `read_delivery` after every file is read, so the checker, the replica loader and the gold set
see the same result. Each step reports what it did as a finding on the delivery (`Delivery.derived`).

- **Exact duplicate rows** (a query that joins a one-to-many table, such as payment methods, repeats
  the header row): one copy kept. Rows with the same key but different values are left for the
  checker's duplicate-key error.
- **Supplier / site view** (`SUPPLIER_SITE_VIEW`, one SQL Developer export joining AP_SUPPLIERS,
  AP_SUPPLIER_SITES_ALL, contacts and IBY payment methods): split into AP_SUPPLIERS and
  AP_SUPPLIER_SITES_ALL. Several rows per site (one per contact and payment method) become one site:
  the payee's default payment method, else the primary one. When the view has no VENDOR_ID, it is
  taken from the invoices that use the supplier's sites.
- **Invoice header without ORG_ID**: taken from the invoice's lines, else from its site.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .readers import Delivery, Row, TableData, num
from .tables import TABLES

VIEW = "SUPPLIER_SITE_VIEW"
SUPPLIER_COLUMNS = ("VENDOR_ID", "SEGMENT1", "VENDOR_NAME", "VENDOR_NAME_ALT", "VENDOR_TYPE_LOOKUP_CODE",
                    "ENABLED_FLAG", "END_DATE_ACTIVE", "SUPPLIER_TERMS_ID", "NUM_1099", "TYPE_1099",
                    "VAT_REGISTRATION_NUM", "PARTY_ID")
SITE_COLUMNS = ("VENDOR_SITE_ID", "VENDOR_ID", "VENDOR_SITE_CODE", "ORG_ID", "ADDRESS_LINE1", "ADDRESS_LINE2",
                "CITY", "STATE", "ZIP", "COUNTRY", "PAY_SITE_FLAG", "PURCHASING_SITE_FLAG", "PRIMARY_PAY_SITE_FLAG",
                "EMAIL_ADDRESS", "TERMS_ID", "TERMS_DATE_BASIS", "PAY_GROUP_LOOKUP_CODE", "INVOICE_CURRENCY_CODE",
                "PAYMENT_CURRENCY_CODE", "ACCTS_PAY_CODE_COMBINATION_ID", "DISTRIBUTION_SET_ID", "INACTIVE_DATE",
                "HOLD_ALL_PAYMENTS_FLAG", "PARTY_SITE_NUMBER")


def _note(d: Delivery, level: str, message: str, items: list[Any] | None = None) -> None:
    d.derived.append((level, message, [str(i) for i in (items or [])]))


def _first(rows: list[Row], column: str) -> Any:
    return next((r[column] for r in rows if r.get(column) is not None), None)


# ------------------------------------------------------------ duplicates
def drop_exact_duplicates(d: Delivery) -> None:
    for name, t in d.tables.items():
        seen: set[tuple] = set()
        kept: list[Row] = []
        for r in t.rows:
            key = tuple(sorted((k, str(v)) for k, v in r.items()))
            if key in seen:
                continue
            seen.add(key)
            kept.append(r)
        if len(kept) < len(t.rows):
            _note(d, "warning", f"{name}: {len(t.rows) - len(kept)} exact duplicate row(s) dropped "
                  "(a one-to-many join in the extract query)")
            t.rows = kept


# ------------------------------------------------------------ supplier view
def _payment_method(rows: list[Row]) -> str | None:
    """The payee's default payment method; else the method flagged primary; else the first one."""
    default = _first(rows, "DEFAULT_PAYMENT_METHOD_CODE")
    if default:
        return default
    primary = [r.get("PRIMARY_PAYMENT_METHOD") for r in rows if (r.get("PRIMARY_PAYMENT_FLAG") or "N") == "Y"]
    return next((p for p in primary if p), None) or _first(rows, "PRIMARY_PAYMENT_METHOD")


def _vendor_ids(d: Delivery, view: list[Row]) -> tuple[dict[str, int], list[str]]:
    """Supplier number -> VENDOR_ID: from the view when it has the column, else from the invoices
    that use one of the supplier's sites. Returns the map and the numbers that disagree."""
    by_number: dict[str, set[int]] = defaultdict(set)
    for r in view:
        if num(r.get("VENDOR_ID")) is not None:
            by_number[str(r["SEGMENT1"])].add(num(r["VENDOR_ID"]))  # type: ignore[arg-type]
    site_vendor: dict[int, set[int]] = defaultdict(set)
    for x in d.rows("AP_INVOICES_ALL"):
        site, vendor = num(x.get("VENDOR_SITE_ID")), num(x.get("VENDOR_ID"))
        if site is not None and vendor is not None:
            site_vendor[site].add(vendor)
    for r in view:
        number = str(r["SEGMENT1"])
        if number not in by_number or not by_number[number]:
            for v in site_vendor.get(num(r.get("VENDOR_SITE_ID")) or -1, set()):
                by_number[number].add(v)
    conflicts = sorted(n for n, ids in by_number.items() if len(ids) > 1)
    return {n: min(ids) for n, ids in by_number.items() if ids}, conflicts


def split_supplier_view(d: Delivery) -> None:
    t = d.tables.pop(VIEW, None)
    if t is None:
        return
    missing = [c for c in TABLES[VIEW].required if c not in t.columns]
    if missing:
        _note(d, "error", f"supplier / site list ({', '.join(t.files)}): required columns missing: "
              f"{', '.join(missing)}")
        return
    vendor_ids, conflicts = _vendor_ids(d, t.rows)
    if conflicts:
        _note(d, "error", "supplier numbers whose sites belong to different VENDOR_IDs in the invoices", conflicts)
    unknown = sorted({str(r["SEGMENT1"]) for r in t.rows} - set(vendor_ids))
    if unknown:
        _note(d, "warning", "suppliers in the supplier / site list without a VENDOR_ID (not in the list, and "
              "no invoice uses their sites): left out", unknown)
    if "VENDOR_ID" not in t.columns and vendor_ids:
        _note(d, "info", "VENDOR_ID not in the supplier / site list: taken from the invoices of each "
              "supplier's sites")

    suppliers: dict[str, Row] = {}
    sites: dict[int, list[Row]] = defaultdict(list)
    for r in t.rows:
        number = str(r["SEGMENT1"])
        if number not in vendor_ids:
            continue
        r = {**r, "VENDOR_ID": vendor_ids[number]}
        suppliers.setdefault(number, {})
        for c in SUPPLIER_COLUMNS:
            if suppliers[number].get(c) is None and r.get(c) is not None:
                suppliers[number][c] = r[c]
        if num(r.get("VENDOR_SITE_ID")) is not None:
            sites[num(r["VENDOR_SITE_ID"])].append(r)  # type: ignore[index]
    supplier_rows = []
    for s in suppliers.values():
        row = {c: s.get(c) for c in SUPPLIER_COLUMNS if c != "SUPPLIER_TERMS_ID"}
        row["TERMS_ID"] = s.get("SUPPLIER_TERMS_ID")
        supplier_rows.append(row)
    site_rows = []
    for rows in sites.values():
        row = {c: _first(rows, c) for c in SITE_COLUMNS}
        row["PAYMENT_METHOD_LOOKUP_CODE"] = _payment_method(rows)
        site_rows.append(row)
    collapsed = len(t.rows) - len(site_rows)
    _note(d, "info", f"supplier / site list split into {len(supplier_rows)} supplier(s) and {len(site_rows)} "
          f"site(s){f' ({collapsed} repeated row(s) per contact / payment method merged)' if collapsed else ''}")
    for name, rows in (("AP_SUPPLIERS", supplier_rows), ("AP_SUPPLIER_SITES_ALL", site_rows)):
        have = d.tables.get(name)
        if have is None:
            d.tables[name] = TableData(spec=TABLES[name], rows=rows, files=list(t.files),
                                       columns=list(dict.fromkeys(k for r in rows for k in r)))
            continue
        key = TABLES[name].key[0]
        known = {num(r.get(key)) for r in have.rows}
        added = [r for r in rows if num(r.get(key)) not in known]
        have.rows += added
        have.files += t.files
        have.columns = list(dict.fromkeys([*have.columns, *(k for r in added for k in r)]))


# ------------------------------------------------------------ header org
def invoice_org(d: Delivery) -> None:
    t = d.tables.get("AP_INVOICES_ALL")
    if t is None or "ORG_ID" in t.columns:
        return
    line_org: dict[int | None, Counter] = defaultdict(Counter)
    for x in d.rows("AP_INVOICE_LINES_ALL"):
        if num(x.get("ORG_ID")) is not None:
            line_org[num(x.get("INVOICE_ID"))][num(x["ORG_ID"])] += 1
    site_org = {num(s.get("VENDOR_SITE_ID")): num(s.get("ORG_ID")) for s in d.rows("AP_SUPPLIER_SITES_ALL")}
    from_lines = from_sites = 0
    unknown = []
    for x in t.rows:
        inv = num(x.get("INVOICE_ID"))
        if line_org.get(inv):
            x["ORG_ID"] = line_org[inv].most_common(1)[0][0]
            from_lines += 1
        elif site_org.get(num(x.get("VENDOR_SITE_ID"))) is not None:
            x["ORG_ID"] = site_org[num(x.get("VENDOR_SITE_ID"))]
            from_sites += 1
        else:
            x["ORG_ID"] = None
            unknown.append(inv)
    t.columns.append("ORG_ID")
    _note(d, "info", f"ORG_ID not in AP_INVOICES_ALL: taken from the lines ({from_lines}) or the site "
          f"({from_sites})")
    if unknown:
        _note(d, "warning", "invoices whose ORG_ID could not be derived (no line, site not delivered)", unknown)


def derive(d: Delivery) -> None:
    drop_exact_duplicates(d)
    split_supplier_view(d)
    invoice_org(d)
