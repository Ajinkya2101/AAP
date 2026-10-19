"""aap-mock: simulate EBS business events so the agents can react (plan 3.6).

    aap-mock receive --po 4500123 --line 1 --qty 2
    aap-mock close-po --po 4500171 [--code "CLOSED FOR INVOICE"]
    aap-mock cancel-line --po 4500188 --line 2
    aap-mock disable-ccid --ccid 01-510-6430-0000
    aap-mock end-date-site --vendor-id 30418 --site DALLAS-HQ [--date 2026-09-01]
    aap-mock import-status --request-id 7340001
    aap-mock import-run [--group-id G] [--all]
    aap-mock seed [--force] [--scale small]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from psycopg.rows import dict_row

from aap.config import get_settings


def _conn(dsn: str | None = None) -> psycopg.Connection:
    return psycopg.connect(dsn or get_settings().ebs_owner_dsn, row_factory=dict_row)  # type: ignore[return-value]


def _po_line(conn: psycopg.Connection, po: str, line: int) -> dict[str, Any]:
    row = conn.execute("""
        SELECT h.po_header_id, h.vendor_id, h.vendor_site_id, h.currency_code, h.org_id,
               l.po_line_id, l.item_id, l.item_description, l.unit_meas_lookup_code, l.unit_price,
               ll.line_location_id, ll.quantity, ll.quantity_received, ll.ship_to_organization_id,
               d.po_distribution_id
        FROM po.po_headers_all h
        JOIN po.po_lines_all l ON l.po_header_id = h.po_header_id
        JOIN po.po_line_locations_all ll ON ll.po_line_id = l.po_line_id
        JOIN po.po_distributions_all d ON d.line_location_id = ll.line_location_id
        WHERE h.segment1 = %s AND l.line_num = %s
        ORDER BY ll.shipment_num LIMIT 1""", (po, line)).fetchone()
    if not row:
        raise SystemExit(f"PO {po} line {line} not found")
    return row  # type: ignore[return-value]


def receive(conn: psycopg.Connection, po: str, line: int, qty: Decimal, when: datetime | None = None) -> int:
    r = _po_line(conn, po, line)
    when = when or datetime.now().replace(microsecond=0)
    hdr = conn.execute("SELECT nextval('po.rcv_shipment_headers_s') AS id, nextval('po.rcv_receipt_num_s') AS num").fetchone()
    conn.execute("""INSERT INTO po.rcv_shipment_headers (shipment_header_id, receipt_num, receipt_source_code,
                        vendor_id, vendor_site_id, shipped_date, ship_to_org_id)
                    VALUES (%s, %s, 'VENDOR', %s, %s, %s, %s)""",
                 (hdr["id"], str(hdr["num"]), r["vendor_id"], r["vendor_site_id"], when, r["ship_to_organization_id"]))
    sl = conn.execute("SELECT nextval('po.rcv_shipment_lines_s') AS id").fetchone()["id"]
    conn.execute("""INSERT INTO po.rcv_shipment_lines (shipment_line_id, shipment_header_id, line_num, item_id,
                        item_description, quantity_shipped, quantity_received, unit_of_measure, po_header_id,
                        po_line_id, po_line_location_id, po_distribution_id, shipment_line_status_code,
                        to_organization_id)
                    VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'FULLY RECEIVED', %s)""",
                 (sl, hdr["id"], r["item_id"], r["item_description"], qty, qty, r["unit_meas_lookup_code"],
                  r["po_header_id"], r["po_line_id"], r["line_location_id"], r["po_distribution_id"],
                  r["ship_to_organization_id"]))
    common = (r["unit_meas_lookup_code"], hdr["id"], sl, r["po_header_id"], r["po_line_id"], r["line_location_id"],
              r["po_distribution_id"], r["vendor_id"], r["vendor_site_id"], r["unit_price"], r["currency_code"],
              r["ship_to_organization_id"])
    rcv = conn.execute("SELECT nextval('po.rcv_transactions_s') AS id").fetchone()["id"]
    for txn_id, parent, ttype in ((rcv, -1, "RECEIVE"), (None, rcv, "DELIVER")):
        conn.execute("""INSERT INTO po.rcv_transactions (transaction_id, parent_transaction_id, transaction_type,
                            transaction_date, quantity, unit_of_measure, shipment_header_id, shipment_line_id,
                            po_header_id, po_line_id, po_line_location_id, po_distribution_id, vendor_id,
                            vendor_site_id, po_unit_price, currency_code, destination_type_code, organization_id)
                        VALUES (coalesce(%s, nextval('po.rcv_transactions_s')), %s, %s, %s, %s,
                                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'EXPENSE', %s)""",
                     (txn_id, parent, ttype, when, qty, *common))
    conn.execute("""UPDATE po.po_line_locations_all
                    SET quantity_received = quantity_received + %s, quantity_accepted = quantity_accepted + %s,
                        last_update_date = localtimestamp(0)
                    WHERE line_location_id = %s""", (qty, qty, r["line_location_id"]))
    conn.execute("""UPDATE po.po_distributions_all SET quantity_delivered = quantity_delivered + %s,
                        last_update_date = localtimestamp(0) WHERE po_distribution_id = %s""",
                 (qty, r["po_distribution_id"]))
    return rcv


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aap-mock", description="Simulate mock EBS business events")
    p.add_argument("--dsn", default=None, help="EBS owner DSN (default EBS_OWNER_DSN)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("receive", help="receive goods against a PO line")
    s.add_argument("--po", required=True)
    s.add_argument("--line", type=int, required=True)
    s.add_argument("--qty", type=Decimal, required=True)
    s.add_argument("--date", type=date.fromisoformat, default=None)

    s = sub.add_parser("close-po", help="close a PO")
    s.add_argument("--po", required=True)
    s.add_argument("--code", default="CLOSED FOR INVOICE",
                   choices=["CLOSED", "CLOSED FOR INVOICE", "CLOSED FOR RECEIVING", "FINALLY CLOSED", "OPEN"])

    s = sub.add_parser("cancel-line", help="cancel the open quantity of a PO line")
    s.add_argument("--po", required=True)
    s.add_argument("--line", type=int, required=True)

    s = sub.add_parser("disable-ccid", help="disable a GL code combination (id or 01-510-6430-0000)")
    s.add_argument("--ccid", required=True)
    s.add_argument("--end-date", type=date.fromisoformat, default=None, help="end-date instead of disabling")

    s = sub.add_parser("end-date-site", help="inactivate a supplier site")
    s.add_argument("--vendor-id", type=int, required=True)
    s.add_argument("--site", required=True, help="VENDOR_SITE_CODE")
    s.add_argument("--date", type=date.fromisoformat, default=None)

    s = sub.add_parser("import-status", help="show a concurrent request and its interface results")
    s.add_argument("--request-id", type=int, required=True)

    s = sub.add_parser("import-run", help="run pending APXIIMPT requests now (no delay)")

    s = sub.add_parser("seed", help="(re)seed the mock EBS")
    s.add_argument("--force", action="store_true")
    s.add_argument("--scale", choices=["small", "default", "large"], default=None)

    a = p.parse_args(argv)

    if a.cmd == "seed":
        from aap.ebs_mock.seed import seed_database
        settings = get_settings()
        seed_database(a.dsn or settings.ebs_owner_dsn, seed=settings.seed, scale=a.scale or settings.seed_scale,
                      force=a.force)
        return 0
    if a.cmd == "import-run":
        from aap.ebs_mock.concurrent_manager import run_pending
        n = run_pending(a.dsn or get_settings().ebs_owner_dsn)
        print(f"processed {n} request(s)")
        return 0

    with _conn(a.dsn) as conn:
        if a.cmd == "receive":
            when = datetime.combine(a.date, datetime.min.time()) if a.date else None
            txn = receive(conn, a.po, a.line, a.qty, when)
            print(f"RECEIVE transaction {txn}: PO {a.po} line {a.line} qty {a.qty}")
        elif a.cmd == "close-po":
            n = conn.execute("""UPDATE po.po_headers_all SET closed_code = %s,
                                    closed_date = CASE WHEN %s = 'OPEN' THEN NULL ELSE localtimestamp(0) END,
                                    last_update_date = localtimestamp(0) WHERE segment1 = %s""",
                             (a.code, a.code, a.po)).rowcount
            conn.execute("""UPDATE po.po_line_locations_all ll SET closed_code = %s FROM po.po_headers_all h
                            WHERE h.po_header_id = ll.po_header_id AND h.segment1 = %s""", (a.code, a.po))
            print(f"PO {a.po}: closed_code={a.code} ({n} header)")
        elif a.cmd == "cancel-line":
            r = _po_line(conn, a.po, a.line)
            conn.execute("""UPDATE po.po_line_locations_all
                            SET quantity_cancelled = quantity - greatest(quantity_received, quantity_billed),
                                cancel_flag = 'Y', last_update_date = localtimestamp(0)
                            WHERE line_location_id = %s""", (r["line_location_id"],))
            conn.execute("UPDATE po.po_lines_all SET cancel_flag = 'Y' WHERE po_line_id = %s", (r["po_line_id"],))
            print(f"PO {a.po} line {a.line}: open quantity cancelled")
        elif a.cmd == "disable-ccid":
            if a.ccid.isdigit():
                where, params = "code_combination_id = %s", [int(a.ccid)]
            else:
                where, params = "segment1 = %s AND segment2 = %s AND segment3 = %s AND segment4 = %s", a.ccid.split("-")
            if a.end_date:
                n = conn.execute(f"UPDATE gl.gl_code_combinations SET end_date_active = %s WHERE {where}",
                                 [a.end_date, *params]).rowcount
            else:
                n = conn.execute(f"UPDATE gl.gl_code_combinations SET enabled_flag = 'N' WHERE {where}",
                                 params).rowcount
            print(f"{n} code combination(s) updated")
        elif a.cmd == "end-date-site":
            n = conn.execute("""UPDATE ap.ap_supplier_sites_all SET inactive_date = %s, last_update_date = localtimestamp(0)
                                WHERE vendor_id = %s AND vendor_site_code = %s""",
                             (a.date or date.today(), a.vendor_id, a.site)).rowcount
            print(f"{n} site(s) inactivated")
        elif a.cmd == "import-status":
            req = conn.execute("SELECT * FROM fnd.fnd_concurrent_requests WHERE request_id = %s",
                               (a.request_id,)).fetchone()
            if not req:
                print("request not found")
                return 1
            rows = conn.execute("""SELECT i.invoice_id, i.invoice_num, i.status,
                                          (SELECT string_agg(reject_lookup_code, ', ')
                                           FROM ap.ap_interface_rejections r
                                           WHERE r.parent_table = 'AP_INVOICES_INTERFACE' AND r.parent_id = i.invoice_id)
                                              AS rejections
                                   FROM ap.ap_invoices_interface i WHERE i.request_id = %s""",
                                (a.request_id,)).fetchall()
            print(json.dumps({"request": {k: req[k] for k in ("request_id", "phase_code", "status_code",
                                                              "argument_text", "completion_text")},
                              "interface": rows}, default=str, indent=2))
        conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
