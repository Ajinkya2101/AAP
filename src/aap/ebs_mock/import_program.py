"""Mock Payables Open Interface Import (APXIIMPT), plan 3.3.

Reads AP_INVOICES_INTERFACE / AP_INVOICE_LINES_INTERFACE for one GROUP_ID,
validates them with real EBS reject codes, creates AP_INVOICES_ALL / LINES /
DISTRIBUTIONS, updates PO billed quantities and places validation holds
(QTY REC, QTY ORD, PRICE) using AP_TOLERANCE_TEMPLATES.

Runs inside "EBS" as the schema owner (called by the mock concurrent manager
or by `aap-mock import-run`). It is not part of the application.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

SYSTEM_USER = 5          # "held_by" for system holds, as in EBS
CENT = Decimal("0.01")


@dataclass
class ImportResult:
    processed: list[int] = field(default_factory=list)     # interface invoice ids
    rejected: list[int] = field(default_factory=list)
    invoice_ids: list[int] = field(default_factory=list)   # AP_INVOICES_ALL ids
    holds: int = 0

    @property
    def completion_text(self) -> str:
        return (f"Normal completion. Invoices processed: {len(self.processed)}, "
                f"rejected: {len(self.rejected)}, holds placed: {self.holds}")


class _Reject(Exception):
    def __init__(self, code: str, table: str = "AP_INVOICES_INTERFACE", parent_id: int | None = None):
        super().__init__(code)
        self.code, self.table, self.parent_id = code, table, parent_id


def _q(conn: psycopg.Connection, sql: str, params: Any = None) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)  # type: ignore[arg-type]
        return cur.fetchall() if cur.description else []


def _one(conn: psycopg.Connection, sql: str, params: Any = None) -> dict[str, Any] | None:
    rows = _q(conn, sql, params)
    return rows[0] if rows else None


def _money(v: Any) -> Decimal:
    return Decimal(v).quantize(CENT, rounding=ROUND_HALF_UP)


def _period(conn: psycopg.Connection, ledger_id: int, d: datetime) -> dict[str, Any] | None:
    return _one(conn, """
        SELECT period_name, closing_status FROM gl.gl_period_statuses
        WHERE application_id = 200 AND ledger_id = %s AND %s BETWEEN start_date AND end_date
          AND adjustment_period_flag = 'N'""", (ledger_id, d))


def _ccid_ok(conn: psycopg.Connection, ccid: int | None, on: datetime) -> bool:
    if not ccid:
        return False
    row = _one(conn, """
        SELECT 1 FROM gl.gl_code_combinations
        WHERE code_combination_id = %s AND enabled_flag = 'Y' AND detail_posting_allowed_flag = 'Y'
          AND summary_flag = 'N'
          AND (start_date_active IS NULL OR start_date_active <= %s)
          AND (end_date_active IS NULL OR end_date_active >= %s)""", (ccid, on, on))
    return row is not None


def _ccid_from_concat(conn: psycopg.Connection, concat: str) -> int | None:
    parts = concat.split("-")
    if len(parts) != 4:
        return None
    row = _one(conn, """SELECT code_combination_id FROM gl.gl_code_combinations
                        WHERE segment1 = %s AND segment2 = %s AND segment3 = %s AND segment4 = %s""", parts)
    return row["code_combination_id"] if row else None


def run_import(conn: psycopg.Connection, *, group_id: str | None, source: str | None = None,
               org_id: int | None = None, request_id: int | None = None) -> ImportResult:
    """Process one batch. Runs in the caller's transaction; each invoice uses a savepoint."""
    result = ImportResult()
    headers = _q(conn, """
        SELECT * FROM ap.ap_invoices_interface
        WHERE status IS NULL
          AND (%(g)s::varchar IS NULL OR group_id = %(g)s)
          AND (%(s)s::varchar IS NULL OR source = %(s)s)
          AND (%(o)s::bigint IS NULL OR org_id = %(o)s OR org_id IS NULL)
        ORDER BY invoice_id
        FOR UPDATE""", {"g": group_id, "s": source, "o": org_id})
    for hdr in headers:
        try:
            with conn.transaction():
                invoice_id, holds = _import_one(conn, hdr, default_org=org_id, request_id=request_id)
            result.processed.append(hdr["invoice_id"])
            result.invoice_ids.append(invoice_id)
            result.holds += holds
        except _Reject as rej:
            _reject(conn, hdr, [rej], request_id)
            result.rejected.append(hdr["invoice_id"])
        except _Rejections as rejs:
            _reject(conn, hdr, rejs.items, request_id)
            result.rejected.append(hdr["invoice_id"])
    return result


class _Rejections(Exception):
    def __init__(self, items: list[_Reject]):
        super().__init__(", ".join(r.code for r in items))
        self.items = items


def _reject(conn: psycopg.Connection, hdr: dict[str, Any], rejs: list[_Reject], request_id: int | None) -> None:
    for r in rejs:
        conn.execute("""INSERT INTO ap.ap_interface_rejections
                        (parent_table, parent_id, reject_lookup_code, request_id, created_by, last_updated_by)
                        VALUES (%s, %s, %s, %s, 5, 5)""",
                     (r.table, r.parent_id or hdr["invoice_id"], r.code, request_id))
    conn.execute("UPDATE ap.ap_invoices_interface SET status = 'REJECTED', request_id = %s, "
                 "last_update_date = localtimestamp(0) WHERE invoice_id = %s", (request_id, hdr["invoice_id"]))


def _import_one(conn: psycopg.Connection, hdr: dict[str, Any], *, default_org: int | None,
                request_id: int | None) -> tuple[int, int]:
    rejects: list[_Reject] = []
    today = datetime.combine(date.today(), datetime.min.time())

    # ---- operating unit
    org_id = hdr["org_id"] or default_org
    if not org_id and hdr["operating_unit"]:
        ou = _one(conn, "SELECT organization_id FROM apps.hr_operating_units WHERE name = %s", (hdr["operating_unit"],))
        org_id = ou["organization_id"] if ou else None
    ou = _one(conn, "SELECT organization_id, set_of_books_id FROM apps.hr_operating_units WHERE organization_id = %s",
              (org_id,)) if org_id else None
    if not ou:
        raise _Reject("INVALID OPERATING UNIT")
    ledger = _one(conn, "SELECT ledger_id, currency_code, chart_of_accounts_id FROM gl.gl_ledgers WHERE ledger_id = %s",
                  (ou["set_of_books_id"],))
    assert ledger is not None

    # ---- supplier
    if not (hdr["vendor_id"] or hdr["vendor_num"] or hdr["vendor_name"]):
        raise _Reject("NO SUPPLIER")
    vendor = _one(conn, """
        SELECT * FROM ap.ap_suppliers
        WHERE (%(id)s::bigint IS NOT NULL AND vendor_id = %(id)s)
           OR (%(id)s::bigint IS NULL AND %(num)s::varchar IS NOT NULL AND segment1 = %(num)s)
           OR (%(id)s::bigint IS NULL AND %(num)s::varchar IS NULL AND vendor_name = %(name)s)""",
                  {"id": hdr["vendor_id"], "num": hdr["vendor_num"], "name": hdr["vendor_name"]})
    if (not vendor or vendor["enabled_flag"] != "Y"
            or (vendor["end_date_active"] and vendor["end_date_active"] < today)):
        raise _Reject("INVALID SUPPLIER")

    # ---- supplier site
    site = _one(conn, """
        SELECT * FROM ap.ap_supplier_sites_all
        WHERE vendor_id = %(v)s AND org_id = %(o)s
          AND ((%(id)s::bigint IS NOT NULL AND vendor_site_id = %(id)s)
               OR (%(id)s::bigint IS NULL AND vendor_site_code = %(code)s))""",
                {"v": vendor["vendor_id"], "o": org_id, "id": hdr["vendor_site_id"], "code": hdr["vendor_site_code"]})
    if (not site or site["pay_site_flag"] != "Y"
            or (site["inactive_date"] and site["inactive_date"] <= today)):
        rejects.append(_Reject("INVALID SUPPLIER SITE"))

    # ---- invoice number / amount / duplicate
    if not hdr["invoice_num"]:
        rejects.append(_Reject("NO INVOICE NUMBER"))
    elif _one(conn, """SELECT 1 FROM ap.ap_invoices_all
                       WHERE vendor_id = %s AND upper(invoice_num) = upper(%s) AND org_id = %s""",
              (vendor["vendor_id"], hdr["invoice_num"], org_id)):
        rejects.append(_Reject("DUPLICATE INVOICE NUMBER"))
    if hdr["invoice_amount"] is None:
        rejects.append(_Reject("INVALID INVOICE AMOUNT"))

    invoice_date = hdr["invoice_date"] or today
    gl_date = hdr["gl_date"] or invoice_date

    # ---- currency and exchange rate
    currency = hdr["invoice_currency_code"] or (site or vendor)["invoice_currency_code"] or ledger["currency_code"]
    if not _one(conn, "SELECT 1 FROM fnd.fnd_currencies WHERE currency_code = %s AND enabled_flag = 'Y'", (currency,)):
        rejects.append(_Reject("INVALID CURRENCY CODE"))
    rate, rate_type, rate_date = None, None, None
    if currency != ledger["currency_code"]:
        rate_type = hdr["exchange_rate_type"] or "Corporate"
        rate_date = hdr["exchange_date"] or gl_date
        rate = hdr["exchange_rate"]
        if rate is None and rate_type != "User":
            r = _one(conn, """SELECT conversion_rate FROM gl.gl_daily_rates
                              WHERE from_currency = %s AND to_currency = %s AND conversion_type = %s
                                AND conversion_date = %s""", (currency, ledger["currency_code"], rate_type, rate_date))
            rate = r["conversion_rate"] if r else None
        if rate is None:
            rejects.append(_Reject("NO EXCHANGE RATE"))

    # ---- terms
    terms_id = hdr["terms_id"]
    if terms_id or hdr["terms_name"]:
        t = _one(conn, """SELECT term_id FROM apps.ap_terms
                          WHERE enabled_flag = 'Y' AND ((%(id)s::bigint IS NOT NULL AND term_id = %(id)s)
                             OR (%(id)s::bigint IS NULL AND name = %(n)s))""",
                 {"id": terms_id, "n": hdr["terms_name"]})
        if not t:
            rejects.append(_Reject("INVALID TERMS"))
        else:
            terms_id = t["term_id"]
    else:
        terms_id = (site or {}).get("terms_id") or vendor["terms_id"]

    # ---- GL period
    period = _period(conn, ledger["ledger_id"], gl_date)
    if not period or period["closing_status"] not in ("O", "F"):
        rejects.append(_Reject("ACCT DATE NOT IN OPEN PD"))

    # ---- header PO
    header_po = None
    if hdr["po_number"]:
        header_po = _find_po(conn, hdr["po_number"], org_id)
        if not header_po:
            rejects.append(_Reject("INVALID PO NUM"))
        elif header_po["vendor_id"] != vendor["vendor_id"]:
            rejects.append(_Reject("INCONSISTENT PO SUPPLIER"))
        elif header_po["currency_code"] != currency:
            rejects.append(_Reject("INCONSISTENT CURR"))

    # ---- lines
    lines = _q(conn, "SELECT * FROM ap.ap_invoice_lines_interface WHERE invoice_id = %s "
                     "ORDER BY line_number NULLS LAST, invoice_line_id", (hdr["invoice_id"],))
    if not lines:
        rejects.append(_Reject("NO LINES"))
    planned: list[dict[str, Any]] = []
    for n, ln in enumerate(lines, start=1):
        ltype = (ln["line_type_lookup_code"] or "ITEM").upper()
        acct_date = ln["accounting_date"] or gl_date
        plan: dict[str, Any] = dict(src=ln, line_number=ln["line_number"] or n, type=ltype,
                                    amount=_money(ln["amount"] or 0), accounting_date=acct_date)
        if ln["amount"] is None:
            rejects.append(_Reject("INVALID LINE AMOUNT", "AP_INVOICE_LINES_INTERFACE", ln["invoice_line_id"]))
        po = None
        if ln["po_header_id"] or ln["po_number"]:
            po = (_find_po(conn, ln["po_number"], org_id) if ln["po_number"] else
                  _one(conn, "SELECT * FROM po.po_headers_all WHERE po_header_id = %s", (ln["po_header_id"],)))
            if not po or not _po_open_for_invoice(po):
                rejects.append(_Reject("INVALID PO NUM", "AP_INVOICE_LINES_INTERFACE", ln["invoice_line_id"]))
                po = None
        elif header_po and ltype == "ITEM" and (ln["po_line_number"] or ln["po_line_id"]):
            po = header_po if _po_open_for_invoice(header_po) else None
        if po and ltype == "ITEM":
            match = _one(conn, """
                SELECT l.po_line_id, l.line_num, ll.line_location_id, d.po_distribution_id, d.code_combination_id,
                       ll.price_override, ll.quantity, ll.quantity_received, ll.quantity_billed,
                       ll.quantity_cancelled, ll.receipt_required_flag, l.item_id, l.item_description,
                       l.unit_meas_lookup_code
                FROM po.po_lines_all l
                JOIN po.po_line_locations_all ll ON ll.po_line_id = l.po_line_id
                JOIN po.po_distributions_all d ON d.line_location_id = ll.line_location_id
                WHERE l.po_header_id = %(h)s
                  AND ((%(lid)s::bigint IS NOT NULL AND l.po_line_id = %(lid)s)
                       OR (%(lid)s::bigint IS NULL AND l.line_num = %(ln)s))
                  AND (%(ship)s::int IS NULL OR ll.shipment_num = %(ship)s)
                ORDER BY ll.shipment_num, d.distribution_num LIMIT 1""",
                         {"h": po["po_header_id"], "lid": ln["po_line_id"], "ln": ln["po_line_number"],
                          "ship": ln["po_shipment_num"]})
            if not match:
                rejects.append(_Reject("INVALID PO LINE NUM", "AP_INVOICE_LINES_INTERFACE", ln["invoice_line_id"]))
            else:
                plan.update(po=po, match=match, ccid=match["code_combination_id"])
        if ltype == "ITEM" and "ccid" not in plan:
            ccid = ln["dist_code_combination_id"]
            if not ccid and ln["dist_code_concatenated"]:
                ccid = _ccid_from_concat(conn, ln["dist_code_concatenated"])
            if not _ccid_ok(conn, ccid, acct_date):
                rejects.append(_Reject("INVALID DISTRIBUTION ACCT", "AP_INVOICE_LINES_INTERFACE", ln["invoice_line_id"]))
            plan["ccid"] = ccid
        planned.append(plan)

    if (hdr["invoice_amount"] is not None and lines
            and sum(p["amount"] for p in planned) != _money(hdr["invoice_amount"])):
        rejects.append(_Reject("LINES AMOUNT MISMATCH"))

    if rejects:
        raise _Rejections(rejects)
    assert site is not None and period is not None

    # ---- create the invoice
    first_item_ccid = next((p["ccid"] for p in planned if p["type"] == "ITEM" and p.get("ccid")),
                           site["accts_pay_code_combination_id"])
    company = _one(conn, "SELECT segment1 FROM gl.gl_code_combinations WHERE code_combination_id = %s",
                   (first_item_ccid,))["segment1"]  # type: ignore[index]
    for p in planned:
        if p["type"] != "ITEM" or not p.get("ccid"):
            acct = {"TAX": "1450" if ledger["currency_code"] == "INR" else None, "FREIGHT": "5020"}.get(p["type"])
            ccid = _ccid_from_concat(conn, f"{company}-000-{acct}-0000") if acct else None
            p["ccid"] = ccid if _ccid_ok(conn, ccid, p["accounting_date"]) else first_item_ccid

    invoice_id = conn.execute("SELECT nextval('ap.ap_invoices_s')").fetchone()[0]  # type: ignore[index]
    amount = _money(hdr["invoice_amount"])
    tax_total = sum((p["amount"] for p in planned if p["type"] == "TAX"), Decimal(0))
    base = (lambda a: _money(a * rate)) if rate else (lambda a: None)
    attrs = {f"attribute{i}": hdr[f"attribute{i}"] for i in range(1, 16)}
    conn.execute("""
        INSERT INTO ap.ap_invoices_all (
            invoice_id, invoice_num, vendor_id, vendor_site_id, org_id, set_of_books_id,
            invoice_type_lookup_code, invoice_date, invoice_received_date, gl_date, invoice_amount, base_amount,
            amount_paid, total_tax_amount, invoice_currency_code, payment_currency_code, exchange_rate,
            exchange_rate_type, exchange_date, terms_id, terms_date, description, source, po_header_id,
            payment_status_flag, wfapproval_status, approval_status, pay_group_lookup_code, payment_method_code,
            accts_pay_code_combination_id, party_id, request_id, created_by, last_updated_by,
            attribute_category, attribute1, attribute2, attribute3, attribute4, attribute5, attribute6,
            attribute7, attribute8, attribute9, attribute10, attribute11, attribute12, attribute13,
            attribute14, attribute15)
        VALUES (%(invoice_id)s, %(invoice_num)s, %(vendor_id)s, %(site_id)s, %(org_id)s, %(sob)s,
            %(type)s, %(invoice_date)s, %(received)s, %(gl_date)s, %(amount)s, %(base)s,
            0, %(tax)s, %(currency)s, %(currency)s, %(rate)s,
            %(rate_type)s, %(rate_date)s, %(terms_id)s, %(invoice_date)s, %(description)s, %(source)s, %(po)s,
            'N', 'NOT REQUIRED', 'NEVER APPROVED', %(pay_group)s, %(pay_method)s,
            %(liability)s, %(party_id)s, %(request_id)s, 1318, 1318,
            %(attribute_category)s, %(attribute1)s, %(attribute2)s, %(attribute3)s, %(attribute4)s,
            %(attribute5)s, %(attribute6)s, %(attribute7)s, %(attribute8)s, %(attribute9)s, %(attribute10)s,
            %(attribute11)s, %(attribute12)s, %(attribute13)s, %(attribute14)s, %(attribute15)s)""",
                 dict(invoice_id=invoice_id, invoice_num=hdr["invoice_num"], vendor_id=vendor["vendor_id"],
                      site_id=site["vendor_site_id"], org_id=org_id, sob=ledger["ledger_id"],
                      type=hdr["invoice_type_lookup_code"] or "STANDARD", invoice_date=invoice_date,
                      received=hdr["invoice_received_date"] or today, gl_date=gl_date, amount=amount,
                      base=base(amount), tax=tax_total, currency=currency, rate=rate, rate_type=rate_type,
                      rate_date=rate_date, terms_id=terms_id, description=hdr["description"],
                      source=hdr["source"], po=header_po["po_header_id"] if header_po else None,
                      pay_group=hdr["pay_group_lookup_code"] or site["pay_group_lookup_code"],
                      pay_method=hdr["payment_method_code"] or site["payment_method_lookup_code"],
                      liability=site["accts_pay_code_combination_id"], party_id=vendor["party_id"],
                      request_id=request_id, attribute_category=hdr["attribute_category"], **attrs))

    tolerance = _one(conn, "SELECT * FROM ap.ap_tolerance_templates WHERE tolerance_id = %s",
                     (site["tolerance_id"],)) or {}
    holds = 0
    for p in planned:
        ln, m = p["src"], p.get("match")
        qty = ln["quantity_invoiced"]
        price = ln["unit_price"]
        if m and qty is None and price is not None and price != 0:
            qty = (p["amount"] / Decimal(price)).quantize(Decimal("0.00001"))
        per = _period(conn, ledger["ledger_id"], p["accounting_date"])
        conn.execute("""
            INSERT INTO ap.ap_invoice_lines_all (
                invoice_id, line_number, line_type_lookup_code, line_source, org_id, description, amount,
                base_amount, quantity_invoiced, unit_price, unit_meas_lookup_code, inventory_item_id,
                item_description, po_header_id, po_line_id, po_line_location_id, po_distribution_id,
                rcv_transaction_id, match_type, accounting_date, period_name, default_dist_ccid,
                tax_classification_code, tax_rate_code, set_of_books_id, created_by, last_updated_by)
            VALUES (%s, %s, %s, 'IMPORTED', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, 1318, 1318)""",
                     (invoice_id, p["line_number"], p["type"], org_id, ln["description"], p["amount"],
                      base(p["amount"]), qty, price, ln["unit_of_meas_lookup_code"] or (m or {}).get("unit_meas_lookup_code"),
                      (m or {}).get("item_id") or ln["inventory_item_id"], (m or {}).get("item_description"),
                      p["po"]["po_header_id"] if m else None, m["po_line_id"] if m else None,
                      m["line_location_id"] if m else None, m["po_distribution_id"] if m else None,
                      ln["rcv_transaction_id"], ("ITEM_TO_RECEIPT" if ln["rcv_transaction_id"] else "ITEM_TO_PO") if m else "NOT_MATCHED",
                      p["accounting_date"], per["period_name"] if per else None, p["ccid"],
                      ln["tax_classification_code"], ln["tax_rate_code"], ledger["ledger_id"]))
        conn.execute("""
            INSERT INTO ap.ap_invoice_distributions_all (
                invoice_distribution_id, invoice_id, invoice_line_number, distribution_line_number,
                line_type_lookup_code, amount, base_amount, quantity_invoiced, unit_price, description,
                accounting_date, period_name, dist_code_combination_id, po_distribution_id, rcv_transaction_id,
                match_status_flag, posted_flag, org_id, set_of_books_id, created_by, last_updated_by)
            VALUES (nextval('ap.ap_invoice_distributions_s'), %s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'N', 'N', %s, %s, 1318, 1318)""",
                     (invoice_id, p["line_number"], p["type"], p["amount"], base(p["amount"]), qty, price,
                      ln["description"], p["accounting_date"], per["period_name"] if per else None, p["ccid"],
                      m["po_distribution_id"] if m else None, ln["rcv_transaction_id"], org_id, ledger["ledger_id"]))
        if m and qty is not None:
            conn.execute("""UPDATE po.po_line_locations_all
                            SET quantity_billed = coalesce(quantity_billed, 0) + %s,
                                amount_billed = coalesce(amount_billed, 0) + %s, last_update_date = localtimestamp(0)
                            WHERE line_location_id = %s""", (qty, p["amount"], m["line_location_id"]))
            conn.execute("""UPDATE po.po_distributions_all
                            SET quantity_billed = coalesce(quantity_billed, 0) + %s,
                                amount_billed = coalesce(amount_billed, 0) + %s, last_update_date = localtimestamp(0)
                            WHERE po_distribution_id = %s""", (qty, p["amount"], m["po_distribution_id"]))
            holds += _validation_holds(conn, invoice_id, org_id, m, Decimal(qty), price, tolerance)

    conn.execute("UPDATE ap.ap_invoices_all SET approval_status = %s WHERE invoice_id = %s",
                 ("NEEDS REAPPROVAL" if holds else "APPROVED", invoice_id))
    conn.execute("UPDATE ap.ap_invoices_interface SET status = 'PROCESSED', request_id = %s, "
                 "last_update_date = localtimestamp(0) WHERE invoice_id = %s", (request_id, hdr["invoice_id"]))
    return invoice_id, holds


def _find_po(conn: psycopg.Connection, number: str, org_id: int) -> dict[str, Any] | None:
    return _one(conn, """SELECT * FROM po.po_headers_all
                         WHERE segment1 = %s AND org_id = %s AND type_lookup_code = 'STANDARD'""", (number, org_id))


def _po_open_for_invoice(po: dict[str, Any]) -> bool:
    return (po["authorization_status"] == "APPROVED" and (po["cancel_flag"] or "N") != "Y"
            and (po["closed_code"] or "OPEN") not in ("CLOSED FOR INVOICE", "FINALLY CLOSED", "CLOSED"))


def _validation_holds(conn: psycopg.Connection, invoice_id: int, org_id: int, m: dict[str, Any],
                      qty: Decimal, price: Any, tol: dict[str, Any]) -> int:
    ll = _one(conn, "SELECT * FROM po.po_line_locations_all WHERE line_location_id = %s", (m["line_location_id"],))
    assert ll is not None
    pct = lambda v: Decimal(v or 0) / 100  # noqa: E731
    holds = []
    ordered = Decimal(ll["quantity"] or 0) - Decimal(ll["quantity_cancelled"] or 0)
    billed = Decimal(ll["quantity_billed"] or 0)
    received = Decimal(ll["quantity_received"] or 0)
    if ll["receipt_required_flag"] == "Y" and billed > received * (1 + pct(tol.get("qty_received_tolerance"))):
        holds.append(("QTY REC", "Quantity billed exceeds quantity received"))
    if billed > ordered * (1 + pct(tol.get("quantity_tolerance"))):
        holds.append(("QTY ORD", "Quantity billed exceeds quantity ordered"))
    if price is not None and Decimal(price) > Decimal(ll["price_override"] or 0) * (1 + pct(tol.get("price_tolerance"))):
        holds.append(("PRICE", "Invoice price exceeds purchase order price"))
    for code, reason in holds:
        conn.execute("""INSERT INTO ap.ap_holds_all (hold_id, invoice_id, line_location_id, hold_lookup_code,
                            hold_reason, hold_date, held_by, status_flag, org_id, created_by, last_updated_by)
                        VALUES (nextval('ap.ap_holds_s'), %s, %s, %s, %s, localtimestamp(0), %s, 'S', %s, 5, 5)""",
                     (invoice_id, m["line_location_id"], code, reason, SYSTEM_USER, org_id))
    return len(holds)
