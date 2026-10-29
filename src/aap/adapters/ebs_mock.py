"""MockEbsAdapter: ErpReadPort (and later ErpWritePort) over the mock EBS database.

All SQL is written against the APPS views with EBS names (search_path=apps), so
the future python-oracledb adapter can reuse it almost verbatim; only the bind
syntax (%(name)s -> :name) changes.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .base import (
    CcidCheck,
    DuplicateHit,
    GlHistory,
    InterfaceLine,
    InterfacePayload,
    PoDetail,
    PoLine,
    PoRelease,
    PoShipment,
    PoSummary,
    Receipt,
    ReferenceData,
    SubmitRef,
    SubmitStatus,
    SupplierContacts,
)

# ------------------------------------------------------------------ SQL
SQL_OPERATING_UNITS = """
SELECT ou.organization_id AS org_id, ou.name, ou.short_code, ou.set_of_books_id AS ledger_id,
       l.name AS ledger_name, l.currency_code, l.chart_of_accounts_id, loc.country,
       concat_ws(', ', loc.address_line_1, loc.town_or_city, loc.region_2, loc.postal_code, loc.country) AS bill_to_text,
       ou.date_to
FROM   hr_operating_units ou
JOIN   gl_ledgers l ON l.ledger_id = ou.set_of_books_id
JOIN   hr_all_organization_units o ON o.organization_id = ou.organization_id
LEFT JOIN hr_locations_all loc ON loc.location_id = o.location_id"""

SQL_SUPPLIERS = """
SELECT s.vendor_id, s.vendor_name, s.vendor_name_alt, s.segment1 AS vendor_number, s.party_id,
       s.vendor_type_lookup_code AS vendor_type,
       coalesce(s.num_1099, s.vat_registration_num, p.jgzz_fiscal_code) AS tax_id,
       s.enabled_flag, s.end_date_active, s.terms_id, s.invoice_currency_code,
       s.payment_method_lookup_code AS payment_method, s.pay_group_lookup_code AS pay_group,
       s.attribute1 AS po_required_flag, s.attribute2 AS default_cost_centre, s.hold_all_payments_flag
FROM   ap_suppliers s
LEFT JOIN hz_parties p ON p.party_id = s.party_id"""

SQL_SITES = """
SELECT vendor_site_id, vendor_id, vendor_site_code, org_id, address_line1, address_line2, address_line3,
       city, state, zip, province, country, email_address, remittance_email, pay_site_flag,
       purchasing_site_flag, primary_pay_site_flag, terms_id, invoice_currency_code, tolerance_id,
       match_option, accts_pay_code_combination_id AS liability_ccid, inactive_date
FROM   ap_supplier_sites_all"""

SQL_BANKS = """
SELECT a.ext_bank_account_id, s.vendor_id, a.bank_account_num, a.iban, a.currency_code, a.country_code,
       b.party_name AS bank_name, coalesce(o.end_date, a.end_date) AS end_date
FROM   iby_ext_bank_accounts a
JOIN   iby_account_owners o ON o.ext_bank_account_id = a.ext_bank_account_id
JOIN   ap_suppliers s ON s.party_id = o.account_owner_party_id
LEFT JOIN hz_parties b ON b.party_id = a.bank_id"""

SQL_TERMS = """
SELECT t.term_id, t.name, t.description, t.enabled_flag, l.due_days, l.due_day_of_month,
       l.discount_percent, l.discount_days,
       (SELECT count(*) FROM ap_terms_lines x WHERE x.term_id = t.term_id) AS installments
FROM   ap_terms t
LEFT JOIN ap_terms_lines l ON l.term_id = t.term_id AND l.sequence_num = 1"""

SQL_CURRENCIES = """
SELECT currency_code, symbol, precision, issuing_territory_code AS territory, enabled_flag
FROM   fnd_currencies"""

SQL_CCIDS = """
SELECT c.code_combination_id AS ccid, c.concatenated_segments AS concatenated,
       c.segment1, c.segment2, c.segment3, c.segment4, c.account_type, c.enabled_flag,
       c.detail_posting_allowed_flag, c.end_date_active,
       v1.description AS company_desc, v2.description AS cost_centre_desc, v3.description AS account_desc
FROM   gl_code_combinations_kfv c
LEFT JOIN fnd_flex_values_vl v1 ON v1.flex_value = c.segment1
       AND v1.flex_value_set_id = (SELECT flex_value_set_id FROM fnd_flex_value_sets WHERE flex_value_set_name = 'ACSE_COMPANY')
LEFT JOIN fnd_flex_values_vl v2 ON v2.flex_value = c.segment2
       AND v2.flex_value_set_id = (SELECT flex_value_set_id FROM fnd_flex_value_sets WHERE flex_value_set_name = 'ACSE_COST_CENTRE')
LEFT JOIN fnd_flex_values_vl v3 ON v3.flex_value = c.segment3
       AND v3.flex_value_set_id = (SELECT flex_value_set_id FROM fnd_flex_value_sets WHERE flex_value_set_name = 'ACSE_ACCOUNT')
WHERE  c.summary_flag = 'N'"""

SQL_CATEGORIES = """
SELECT category_id, concat_ws('-', segment1, segment2, segment3, segment4) AS code, description, enabled_flag,
       disable_date
FROM   mtl_categories_vl"""

SQL_UOMS = "SELECT unit_of_measure, uom_code, uom_class, disable_date FROM mtl_units_of_measure"

SQL_TAX_RATES = """
SELECT tax_rate_id, tax_rate_code, tax_regime_code, tax, percentage_rate, active_flag, effective_to
FROM   zx_rates_b"""

SQL_TOLERANCES = """
SELECT tolerance_id, tolerance_name AS name, price_tolerance, quantity_tolerance, qty_received_tolerance
FROM   ap_tolerance_templates"""

SQL_PERIODS = """
SELECT ledger_id, period_name, start_date, end_date, closing_status
FROM   gl_period_statuses
WHERE  application_id = 200 AND adjustment_period_flag = 'N'"""

SQL_PO = """
SELECT h.po_header_id, h.segment1 AS po_number, h.type_lookup_code, h.org_id, h.vendor_id, h.vendor_site_id,
       h.currency_code, h.authorization_status, h.closed_code AS header_closed_code, h.cancel_flag AS header_cancel,
       p.full_name AS buyer_name, p.email_address AS buyer_email,
       l.po_line_id, l.line_num, l.item_id, msi.segment1 AS item_number, l.item_description, l.category_id,
       l.unit_meas_lookup_code, l.unit_price, l.quantity AS line_quantity, l.closed_code AS line_closed_code,
       l.cancel_flag AS line_cancel,
       ll.line_location_id, ll.shipment_num, ll.quantity, ll.quantity_received, ll.quantity_billed,
       ll.quantity_cancelled, ll.price_override, ll.receipt_required_flag, ll.match_option,
       ll.closed_code AS shipment_closed_code, ll.need_by_date, ll.po_release_id, r.release_num,
       d.po_distribution_id, d.code_combination_id, c.concatenated_segments
FROM   po_headers_all h
JOIN   po_lines_all l ON l.po_header_id = h.po_header_id
LEFT JOIN po_line_locations_all ll ON ll.po_line_id = l.po_line_id
LEFT JOIN po_releases_all r ON r.po_release_id = ll.po_release_id
LEFT JOIN po_distributions_all d ON d.line_location_id = ll.line_location_id AND d.distribution_num = 1
LEFT JOIN gl_code_combinations_kfv c ON c.code_combination_id = d.code_combination_id
LEFT JOIN mtl_system_items_b msi ON msi.inventory_item_id = l.item_id
       AND msi.organization_id = (SELECT min(organization_id) FROM mtl_system_items_b x WHERE x.inventory_item_id = l.item_id)
LEFT JOIN per_all_people_f p ON p.person_id = h.agent_id
       AND localtimestamp BETWEEN p.effective_start_date AND p.effective_end_date
WHERE  h.segment1 = %(po_number)s
  AND  (%(org_id)s::bigint IS NULL OR h.org_id = %(org_id)s)
ORDER BY h.type_lookup_code DESC, l.line_num, r.release_num NULLS FIRST, ll.shipment_num"""

SQL_PO_RELEASES = """
SELECT po_release_id, release_num, authorization_status, closed_code, cancel_flag
FROM   po_releases_all WHERE po_header_id = %s ORDER BY release_num"""

# Open standard POs of a vendor with enough open amount and a matching unit price
# (WORKED-EXAMPLE 3, step 6a fallback search).
SQL_SEARCH_OPEN_POS = """
WITH open_lines AS (
    SELECT h.po_header_id, h.segment1 AS po_number, h.vendor_site_id, h.currency_code, l.unit_price,
           (ll.quantity - coalesce(ll.quantity_cancelled, 0) - coalesce(ll.quantity_billed, 0)) * ll.price_override
               AS open_amount
    FROM   po_headers_all h
    JOIN   po_lines_all l ON l.po_header_id = h.po_header_id
    JOIN   po_line_locations_all ll ON ll.po_line_id = l.po_line_id
    WHERE  h.vendor_id = %(vendor_id)s
      AND  h.currency_code = %(currency)s
      AND  h.type_lookup_code = 'STANDARD'
      AND  h.authorization_status = 'APPROVED'
      AND  coalesce(h.cancel_flag, 'N') = 'N'
      AND  coalesce(h.closed_code, 'OPEN') NOT IN ('CLOSED', 'FINALLY CLOSED', 'CLOSED FOR INVOICE')
      AND  coalesce(ll.cancel_flag, 'N') = 'N'
)
SELECT po_header_id, po_number, vendor_site_id, currency_code, sum(open_amount) AS open_amount,
       array_agg(DISTINCT unit_price) FILTER (WHERE unit_price = ANY(%(prices)s::numeric[])) AS matching_prices
FROM   open_lines
GROUP BY po_header_id, po_number, vendor_site_id, currency_code
HAVING sum(open_amount) >= %(min_open)s
   AND (cardinality(%(prices)s::numeric[]) = 0 OR bool_or(unit_price = ANY(%(prices)s::numeric[])))
ORDER BY po_number"""

SQL_RECEIPTS = """
SELECT t.transaction_id, t.parent_transaction_id, t.transaction_type, t.transaction_date, t.quantity,
       t.po_line_location_id, sh.receipt_num
FROM   rcv_transactions t
LEFT JOIN rcv_shipment_headers sh ON sh.shipment_header_id = t.shipment_header_id
WHERE  t.po_line_location_id = ANY(%(ids)s)
ORDER BY t.transaction_date, t.transaction_id"""

SQL_DUPLICATE = """
SELECT invoice_id, invoice_num, vendor_id, org_id, invoice_date, invoice_amount,
       CASE WHEN upper(invoice_num) = upper(%(num)s) THEN 'same_number' ELSE 'same_amount_and_date' END AS reason
FROM   ap_invoices_all
WHERE  vendor_id = %(vendor_id)s
  AND  cancelled_date IS NULL
  AND  (upper(invoice_num) = upper(%(num)s)
        OR (%(amount)s::numeric IS NOT NULL AND %(inv_date)s::date IS NOT NULL
            AND invoice_amount = %(amount)s AND invoice_date::date = %(inv_date)s))"""

SQL_CCID_BY_SEGMENTS = """
SELECT code_combination_id, concatenated_segments, enabled_flag, detail_posting_allowed_flag, summary_flag,
       start_date_active, end_date_active
FROM   gl_code_combinations_kfv
WHERE  segment1 = %s AND segment2 = %s AND segment3 = %s AND segment4 = %s"""

SQL_CCID_BY_ID = """
SELECT code_combination_id, concatenated_segments, enabled_flag, detail_posting_allowed_flag, summary_flag,
       start_date_active, end_date_active
FROM   gl_code_combinations_kfv
WHERE  code_combination_id = %s"""

SQL_GL_HISTORY = """
SELECT d.invoice_distribution_id, i.vendor_id, i.org_id, coalesce(d.description, l.description) AS description,
       d.dist_code_combination_id AS ccid, c.concatenated_segments, c.segment3 AS natural_account,
       d.amount, i.invoice_date,
       concat_ws('-', cat.segment1, cat.segment2, cat.segment3, cat.segment4) AS category_code
FROM   ap_invoice_distributions_all d
JOIN   ap_invoices_all i ON i.invoice_id = d.invoice_id
JOIN   ap_invoice_lines_all l ON l.invoice_id = d.invoice_id AND l.line_number = d.invoice_line_number
JOIN   gl_code_combinations_kfv c ON c.code_combination_id = d.dist_code_combination_id
LEFT JOIN mtl_item_categories ic ON ic.inventory_item_id = l.inventory_item_id
       AND ic.organization_id = (SELECT min(organization_id) FROM mtl_item_categories y WHERE y.inventory_item_id = l.inventory_item_id)
LEFT JOIN mtl_categories_b cat ON cat.category_id = ic.category_id
WHERE  d.line_type_lookup_code = 'ITEM'
  AND  i.cancelled_date IS NULL
  AND  (%(vendor_id)s::bigint IS NULL OR i.vendor_id = %(vendor_id)s)
  AND  (%(since)s::date IS NULL OR i.invoice_date >= %(since)s)
ORDER BY d.invoice_distribution_id"""

SQL_CONTACTS = """
SELECT s.vendor_site_id, s.vendor_id, s.email_address, s.remittance_email
FROM   ap_supplier_sites_all s
WHERE  s.vendor_site_id = %s"""

SQL_BUYER_EMAILS = """
SELECT DISTINCT p.email_address
FROM   po_headers_all h
JOIN   per_all_people_f p ON p.person_id = h.agent_id
WHERE  h.vendor_site_id = %s AND p.email_address IS NOT NULL
  AND  localtimestamp BETWEEN p.effective_start_date AND p.effective_end_date
LIMIT  5"""

SQL_RATE = """
SELECT conversion_rate FROM gl_daily_rates
WHERE  from_currency = %s AND to_currency = %s AND conversion_type = %s AND conversion_date = %s"""

SQL_PERIOD_FOR_DATE = """
SELECT period_name, closing_status FROM gl_period_statuses
WHERE  application_id = 200 AND ledger_id = %s AND %s BETWEEN start_date AND end_date
  AND  adjustment_period_flag = 'N'"""


# ---- write side (apps_intf)
SQL_INTF_BY_KEY = """
    SELECT invoice_id, group_id, request_id FROM ap_invoices_interface
    WHERE attribute15 = %s ORDER BY invoice_id DESC LIMIT 1"""

SQL_INSERT_INTF_HEADER = """
    INSERT INTO ap_invoices_interface (invoice_id, invoice_num, invoice_type_lookup_code, invoice_date, gl_date,
        vendor_id, vendor_site_id, vendor_site_code, invoice_amount, invoice_currency_code, exchange_rate_type,
        exchange_date, terms_id, terms_name, po_number, description, source, group_id, org_id,
        calc_tax_during_import_flag, attribute15)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'N', %s)"""

SQL_INSERT_INTF_LINE = """
    INSERT INTO ap_invoice_lines_interface (invoice_id, invoice_line_id, line_number, line_type_lookup_code, amount,
        description, quantity_invoiced, unit_price, unit_of_meas_lookup_code, po_number, release_num, po_line_number,
        dist_code_concatenated, tax_rate_code, accounting_date, org_id)
    VALUES (%s, nextval('ap.ap_invoice_lines_interface_s'), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"""

SQL_INTF_REJECTS = """
    SELECT DISTINCT r.reject_lookup_code FROM ap_interface_rejections r
    WHERE (r.parent_table = 'AP_INVOICES_INTERFACE' AND r.parent_id = %(i)s)
       OR (r.parent_table = 'AP_INVOICE_LINES_INTERFACE' AND r.parent_id IN
           (SELECT invoice_line_id FROM ap_invoice_lines_interface WHERE invoice_id = %(i)s))
    ORDER BY 1"""


def _d(v: Any) -> date | None:
    if v is None:
        return None
    return v.date() if isinstance(v, datetime) else v


def _dec(v: Any) -> Decimal:
    return Decimal(v) if v is not None else Decimal(0)


class MockEbsAdapter:
    """Read side over apps_ro (pooled); write side over apps_intf (short connections)."""

    def __init__(self, ro_dsn: str, intf_dsn: str | None = None, *, pool_size: int = 5):
        kwargs = {"options": "-c search_path=apps", "autocommit": True, "row_factory": dict_row}
        self._ro = ConnectionPool(ro_dsn, kwargs=kwargs, min_size=1, max_size=pool_size, open=False)
        self._intf_dsn = intf_dsn
        self._opened = False

    def _pool(self) -> ConnectionPool:
        if not self._opened:
            self._ro.open(wait=True, timeout=30)
            self._opened = True
        return self._ro

    def close(self) -> None:
        if self._opened:
            self._ro.close()
            self._opened = False

    def _all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        with self._pool().connection() as conn:
            return conn.execute(sql, params).fetchall()  # type: ignore[arg-type,return-value]

    # ------------------------------------------------------ reference data
    def load_reference_data(self) -> ReferenceData:
        data = ReferenceData()
        data.operating_units = [dict(r, date_to=_d(r["date_to"])) for r in self._all(SQL_OPERATING_UNITS)]
        data.suppliers = [dict(r, end_date_active=_d(r["end_date_active"])) for r in self._all(SQL_SUPPLIERS)]
        data.supplier_sites = [dict(r, inactive_date=_d(r["inactive_date"])) for r in self._all(SQL_SITES)]
        data.supplier_banks = [dict(r, end_date=_d(r["end_date"])) for r in self._all(SQL_BANKS)]
        data.terms = self._all(SQL_TERMS)
        data.currencies = self._all(SQL_CURRENCIES)
        data.ccids = [dict(r, end_date_active=_d(r["end_date_active"])) for r in self._all(SQL_CCIDS)]
        data.categories = [dict(r, disable_date=_d(r["disable_date"])) for r in self._all(SQL_CATEGORIES)]
        data.uoms = [dict(r, disable_date=_d(r["disable_date"])) for r in self._all(SQL_UOMS)]
        data.tax_rates = [dict(r, effective_to=_d(r["effective_to"])) for r in self._all(SQL_TAX_RATES)]
        data.tolerances = self._all(SQL_TOLERANCES)
        data.gl_periods = [dict(r, start_date=_d(r["start_date"]), end_date=_d(r["end_date"]))
                           for r in self._all(SQL_PERIODS)]
        return data

    # ----------------------------------------------------------------- PO
    def get_po(self, po_number: str, org_id: int | None = None) -> PoDetail | None:
        rows = self._all(SQL_PO, {"po_number": po_number, "org_id": org_id})
        if not rows:
            return None
        first = rows[0]
        po = PoDetail(
            po_header_id=first["po_header_id"], po_number=first["po_number"], type=first["type_lookup_code"],
            org_id=first["org_id"], vendor_id=first["vendor_id"], vendor_site_id=first["vendor_site_id"],
            currency_code=first["currency_code"], authorization_status=first["authorization_status"],
            closed_code=first["header_closed_code"], cancelled=(first["header_cancel"] or "N") == "Y",
            buyer_name=first["buyer_name"], buyer_email=first["buyer_email"],
        )
        lines: dict[int, PoLine] = {}
        for r in rows:
            if r["po_header_id"] != po.po_header_id:
                continue
            line = lines.get(r["po_line_id"])
            if line is None:
                line = PoLine(po_line_id=r["po_line_id"], line_num=r["line_num"], item_id=r["item_id"],
                              item_number=r["item_number"], item_description=r["item_description"],
                              category_id=r["category_id"], uom=r["unit_meas_lookup_code"],
                              unit_price=_dec(r["unit_price"]), quantity=r["line_quantity"],
                              closed_code=r["line_closed_code"], cancelled=(r["line_cancel"] or "N") == "Y")
                lines[r["po_line_id"]] = line
            if r["line_location_id"] is not None:
                line.shipments.append(PoShipment(
                    line_location_id=r["line_location_id"], shipment_num=r["shipment_num"],
                    quantity=_dec(r["quantity"]), quantity_received=_dec(r["quantity_received"]),
                    quantity_billed=_dec(r["quantity_billed"]), quantity_cancelled=_dec(r["quantity_cancelled"]),
                    price_override=_dec(r["price_override"]), receipt_required=r["receipt_required_flag"] == "Y",
                    match_option=r["match_option"], closed_code=r["shipment_closed_code"],
                    need_by_date=_d(r["need_by_date"]), po_distribution_id=r["po_distribution_id"],
                    ccid=r["code_combination_id"], ccid_concatenated=r["concatenated_segments"],
                    po_release_id=r["po_release_id"], release_num=r["release_num"]))
        po.lines = list(lines.values())
        if po.blanket:
            po.releases = [PoRelease(po_release_id=r["po_release_id"], release_num=r["release_num"],
                                     authorization_status=r["authorization_status"], closed_code=r["closed_code"],
                                     cancelled=(r["cancel_flag"] or "N") == "Y")
                           for r in self._all(SQL_PO_RELEASES, (po.po_header_id,))]
        return po

    def search_open_pos(self, vendor_id: int, currency: str, min_open_amount: Decimal,
                        unit_prices: list[Decimal]) -> list[PoSummary]:
        rows = self._all(SQL_SEARCH_OPEN_POS, {"vendor_id": vendor_id, "currency": currency,
                                               "min_open": min_open_amount,
                                               "prices": [Decimal(p) for p in unit_prices]})
        return [PoSummary(po_header_id=r["po_header_id"], po_number=r["po_number"],
                          vendor_site_id=r["vendor_site_id"], currency_code=r["currency_code"],
                          open_amount=_dec(r["open_amount"]), matching_prices=r["matching_prices"] or [])
                for r in rows]

    def get_receipts(self, po_line_location_ids: list[int]) -> list[Receipt]:
        if not po_line_location_ids:
            return []
        return [Receipt(transaction_id=r["transaction_id"], parent_transaction_id=r["parent_transaction_id"],
                        transaction_type=r["transaction_type"], transaction_date=_d(r["transaction_date"]),
                        quantity=_dec(r["quantity"]), po_line_location_id=r["po_line_location_id"],
                        receipt_num=r["receipt_num"])
                for r in self._all(SQL_RECEIPTS, {"ids": list(po_line_location_ids)})]

    # ---------------------------------------------------------- invoices
    def find_duplicate(self, vendor_id: int, invoice_num: str, amount: Decimal | None = None,
                       invoice_date: date | None = None) -> list[DuplicateHit]:
        rows = self._all(SQL_DUPLICATE, {"vendor_id": vendor_id, "num": invoice_num, "amount": amount,
                                         "inv_date": invoice_date})
        return [DuplicateHit(invoice_id=r["invoice_id"], invoice_num=r["invoice_num"], vendor_id=r["vendor_id"],
                             org_id=r["org_id"], invoice_date=_d(r["invoice_date"]),
                             invoice_amount=_dec(r["invoice_amount"]), reason=r["reason"]) for r in rows]

    # ---------------------------------------------------------------- GL
    def ccid_valid(self, segments: str | list[str] | int, on_date: date | None = None) -> CcidCheck:
        on = on_date or date.today()
        if isinstance(segments, int):
            rows = self._all(SQL_CCID_BY_ID, (segments,))
            concat = str(segments)
        else:
            parts = segments.split("-") if isinstance(segments, str) else list(segments)
            concat = "-".join(parts)
            rows = self._all(SQL_CCID_BY_SEGMENTS, tuple(parts)) if len(parts) == 4 else []
        if not rows:
            return CcidCheck(ccid=None, concatenated=concat, exists=False, enabled=False, postable=False,
                             active_on_date=False)
        r = rows[0]
        start, end = _d(r["start_date_active"]), _d(r["end_date_active"])
        return CcidCheck(ccid=r["code_combination_id"], concatenated=r["concatenated_segments"], exists=True,
                         enabled=r["enabled_flag"] == "Y",
                         postable=r["detail_posting_allowed_flag"] == "Y" and r["summary_flag"] == "N",
                         active_on_date=(start is None or start <= on) and (end is None or end >= on))

    def get_gl_history(self, vendor_id: int | None = None, since: date | None = None) -> Iterator[GlHistory]:
        with self._pool().connection() as conn, conn.transaction(), conn.cursor(name="gl_history") as cur:
            cur.itersize = 2000
            cur.execute(SQL_GL_HISTORY, {"vendor_id": vendor_id, "since": since})
            for r in cur:
                yield GlHistory(invoice_distribution_id=r["invoice_distribution_id"], vendor_id=r["vendor_id"],
                                org_id=r["org_id"], description=r["description"], ccid=r["ccid"],
                                concatenated=r["concatenated_segments"], natural_account=r["natural_account"],
                                amount=_dec(r["amount"]), invoice_date=_d(r["invoice_date"]),
                                category_code=r["category_code"] or None)

    def get_exchange_rate(self, from_currency: str, to_currency: str, on_date: date,
                          rate_type: str = "Corporate") -> Decimal | None:
        if from_currency == to_currency:
            return Decimal(1)
        rows = self._all(SQL_RATE, (from_currency, to_currency, rate_type,
                                    datetime.combine(on_date, datetime.min.time())))
        return _dec(rows[0]["conversion_rate"]) if rows else None

    def period_status(self, ledger_id: int, on_date: date) -> tuple[str, str] | None:
        rows = self._all(SQL_PERIOD_FOR_DATE, (ledger_id, datetime.combine(on_date, datetime.min.time())))
        return (rows[0]["period_name"], rows[0]["closing_status"]) if rows else None

    # ---------------------------------------------------------- contacts
    def get_supplier_contacts(self, vendor_site_id: int) -> SupplierContacts | None:
        rows = self._all(SQL_CONTACTS, (vendor_site_id,))
        if not rows:
            return None
        r = rows[0]
        emails = [e.strip() for e in (r["email_address"] or "").replace(";", ",").split(",") if e.strip()]
        buyers = [b["email_address"] for b in self._all(SQL_BUYER_EMAILS, (vendor_site_id,))]
        return SupplierContacts(vendor_site_id=r["vendor_site_id"], vendor_id=r["vendor_id"], emails=emails,
                                remittance_email=r["remittance_email"], buyer_emails=buyers)

    # ------------------------------------------------------- write side
    # apps_intf may only insert into the two interface tables, submit APXIIMPT and
    # call the public hold API (as in EBS). Read-backs go through apps views.
    def _intf(self) -> psycopg.Connection:
        if not self._intf_dsn:
            raise RuntimeError("EBS_INTF_DSN is not configured")
        return psycopg.connect(self._intf_dsn, options="-c search_path=apps", row_factory=dict_row)  # type: ignore[return-value]

    def to_payload(self, invoice: Any) -> InterfacePayload:
        """CanonicalInvoice (approved version) -> Payables Open Interface rows."""
        from aap.config import get_settings

        inv = invoice
        lines: list[InterfaceLine] = []
        for ln in inv.lines:
            amount = ln.amount.value
            if amount is None and ln.quantity.value is not None and ln.unit_price.value is not None:
                amount = (ln.quantity.value * ln.unit_price.value).quantize(Decimal("0.01"))
            item = ln.line_type == "ITEM"
            paired = item and ln.po_line_number.value is not None
            lines.append(InterfaceLine(
                line_number=len(lines) + 1, line_type_lookup_code=ln.line_type, amount=amount or Decimal(0),
                description=(ln.description.value or "")[:240] or None,
                quantity_invoiced=ln.quantity.value if item else None,
                unit_price=ln.unit_price.value if item else None,
                unit_of_meas_lookup_code=ln.uom.value if item else None,
                po_number=(ln.po_number.value or inv.po_number.value) if paired else None,
                release_num=(ln.po_release_num.value or inv.po_release_num.value) if paired else None,
                po_line_number=ln.po_line_number.value if paired else None,
                dist_code_concatenated=None if paired else ln.dist_account.value,
                tax_rate_code=ln.tax_rate_code.value))
        # Header-level tax / freight become their own lines (INVOICE_AMOUNT = sum of lines).
        for kind, value in (("TAX", inv.tax_amount.value), ("FREIGHT", inv.freight_amount.value)):
            if value and not any(x.line_type_lookup_code == kind for x in lines):
                lines.append(InterfaceLine(line_number=len(lines) + 1, line_type_lookup_code=kind, amount=value,
                                           description=kind.title()))
        amount = inv.invoice_amount.value
        foreign = bool(inv.ledger_currency.value) and inv.currency_code.value != inv.ledger_currency.value
        inv_type = "CREDIT" if amount is not None and amount < 0 else (inv.invoice_type.value or "STANDARD")
        return InterfacePayload(
            invoice_num=inv.invoice_num.value, invoice_type_lookup_code=inv_type,
            invoice_date=inv.invoice_date.value, gl_date=inv.gl_date.value, vendor_id=inv.vendor_id.value,
            vendor_site_id=inv.vendor_site_id.value, vendor_site_code=inv.vendor_site_code.value,
            invoice_amount=amount, invoice_currency_code=inv.currency_code.value,
            exchange_rate_type=(inv.exchange_rate_type.value or "Corporate") if foreign else None,
            exchange_date=(inv.gl_date.value or inv.invoice_date.value) if foreign else None,
            terms_id=inv.terms_id.value, terms_name=None if inv.terms_id.value else inv.terms_name.value,
            po_number=inv.po_number.value, description=(inv.description.value or "")[:240] or None,
            source=get_settings().invoice_source, org_id=inv.org_id.value, lines=lines)

    def submit(self, payload: InterfacePayload, idempotency_key: str) -> SubmitRef:
        """Insert into the open interface and submit APXIIMPT for a new GROUP_ID.
        Idempotent: an interface row with ATTRIBUTE15 = key is returned, not inserted again (R-07)."""
        with self._intf() as conn:
            prev = conn.execute(SQL_INTF_BY_KEY, (idempotency_key,)).fetchone()
            if prev:
                return SubmitRef(idempotency_key=idempotency_key, group_id=prev["group_id"],
                                 interface_invoice_id=prev["invoice_id"],
                                 request_id=prev["request_id"] or self._request_for_group(conn, prev["group_id"]))
            ids = conn.execute("SELECT nextval('ap.ap_invoices_interface_s') AS inv, "
                               "nextval('ap.ap_interface_groups_s') AS grp").fetchone()
            inv_id, group_id = ids["inv"], f"AAP-{ids['grp']}"
            h = payload
            conn.execute(SQL_INSERT_INTF_HEADER, (
                inv_id, h.invoice_num, h.invoice_type_lookup_code, h.invoice_date, h.gl_date, h.vendor_id,
                h.vendor_site_id, h.vendor_site_code, h.invoice_amount, h.invoice_currency_code, h.exchange_rate_type,
                h.exchange_date, h.terms_id, h.terms_name, h.po_number, h.description, h.source, group_id, h.org_id,
                idempotency_key))
            for ln in h.lines:
                conn.execute(SQL_INSERT_INTF_LINE, (
                    inv_id, ln.line_number, ln.line_type_lookup_code, ln.amount, ln.description,
                    ln.quantity_invoiced, ln.unit_price, ln.unit_of_meas_lookup_code, ln.po_number, ln.release_num,
                    ln.po_line_number, ln.dist_code_concatenated, ln.tax_rate_code, ln.accounting_date, h.org_id))
            request_id = conn.execute(
                "SELECT fnd_request_submit_request('SQLAP', 'APXIIMPT', %s, NULL, false, %s, %s, %s) AS r",
                (f"AAP {idempotency_key}", str(h.org_id), h.source, group_id)).fetchone()["r"]
            conn.commit()
        if not request_id:
            raise RuntimeError("FND_REQUEST.SUBMIT_REQUEST returned 0 (APXIIMPT not submitted)")
        return SubmitRef(idempotency_key=idempotency_key, group_id=group_id, interface_invoice_id=inv_id,
                         request_id=request_id)

    @staticmethod
    def _request_for_group(conn: psycopg.Connection, group_id: str) -> int | None:
        r = conn.execute("SELECT max(request_id) AS r FROM fnd_concurrent_requests WHERE argument3 = %s",
                         (group_id,)).fetchone()
        return r["r"] if r else None  # type: ignore[index]

    def get_status(self, ref: SubmitRef) -> SubmitStatus:
        with self._intf() as conn:
            row = conn.execute("SELECT status, request_id FROM ap_invoices_interface WHERE invoice_id = %s",
                               (ref.interface_invoice_id,)).fetchone()
            if row is None:
                return SubmitStatus(state="error", errors=["interface row not found"])
            req = conn.execute("SELECT phase_code, status_code, completion_text FROM fnd_concurrent_requests "
                               "WHERE request_id = %s", (row["request_id"] or ref.request_id,)).fetchone()
            detail = req["completion_text"] if req else None
            if row["status"] == "PROCESSED":
                inv = conn.execute("SELECT invoice_id FROM ap_invoices_all WHERE attribute15 = %s "
                                   "ORDER BY invoice_id DESC LIMIT 1", (ref.idempotency_key,)).fetchone()
                holds = [h["hold_lookup_code"] for h in conn.execute(
                    "SELECT hold_lookup_code FROM ap_holds_all WHERE invoice_id = %s "
                    "AND release_lookup_code IS NULL ORDER BY hold_id", (inv["invoice_id"],))] if inv else []
                return SubmitStatus(state="created", oracle_invoice_id=inv["invoice_id"] if inv else None,
                                    holds=holds, detail=detail)
            if row["status"] == "REJECTED":
                errors = [r["reject_lookup_code"] for r in conn.execute(SQL_INTF_REJECTS,
                                                                        {"i": ref.interface_invoice_id})]
                return SubmitStatus(state="error", errors=errors or ["REJECTED"], detail=detail)
            if req and req["phase_code"] == "C" and req["status_code"] in ("E", "X", "D"):
                return SubmitStatus(state="error", detail=detail, errors=[
                    f"APXIIMPT request {ref.request_id} ended with status {req['status_code']}"])
            return SubmitStatus(state="pending", detail=detail)

    def apply_hold(self, oracle_invoice_id: int, hold_code: str, reason: str) -> None:
        """AP_HOLDS_PKG.INSERT_SINGLE_HOLD (idempotent for an unreleased hold of the same code)."""
        with self._intf() as conn:
            conn.execute("SELECT ap_holds_pkg_insert_single_hold(%s, %s, 'INVOICE HOLD REASON', %s)",
                         (oracle_invoice_id, hold_code, reason[:240]))
            conn.commit()
