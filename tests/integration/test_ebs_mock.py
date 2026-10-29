"""Mock EBS: invariants, APPS views, adapter roles, APXIIMPT (plan Phase 1 'done when')."""

from __future__ import annotations

import psycopg
import pytest
from psycopg.rows import dict_row

from aap.ebs_mock.concurrent_manager import run_pending
from aap.ebs_mock.seed.invariants import check

pytestmark = pytest.mark.integration

WORKED_EXAMPLE_SQL = """
SELECT h.segment1 AS po_number, h.vendor_id, h.currency_code, h.closed_code,
       l.line_num, l.item_description, l.unit_price,
       ll.quantity, ll.quantity_received, ll.quantity_billed
FROM   po_headers_all h
JOIN   po_lines_all l            ON l.po_header_id = h.po_header_id
JOIN   po_line_locations_all ll  ON ll.po_line_id  = l.po_line_id
WHERE  h.segment1 = %(po_number)s
ORDER BY l.line_num"""


def test_invariants_hold(seeded_ebs, env):
    assert seeded_ebs["ap.ap_suppliers"] > 40
    with psycopg.connect(env["EBS_OWNER_DSN"]) as conn:
        assert check(conn) == {}


def test_worked_example_sql_runs_unchanged_as_apps_ro(seeded_ebs, env):
    with psycopg.connect(env["EBS_RO_DSN"], row_factory=dict_row) as conn:
        rows = conn.execute(WORKED_EXAMPLE_SQL, {"po_number": "4500123"}).fetchall()
    assert [r["line_num"] for r in rows] == [1, 2, 3, 4]
    assert rows[0]["vendor_id"] == 30418
    assert (rows[0]["quantity"], rows[0]["quantity_received"]) == (10, 8)
    assert rows[3]["item_description"] == "LAPTOP, 16 IN, ENGINEERING"


def test_adapter_roles_cannot_touch_base_tables(seeded_ebs, env):
    with psycopg.connect(env["EBS_RO_DSN"]) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("SELECT count(*) FROM ap.ap_invoices_all")
    with psycopg.connect(env["EBS_INTF_DSN"]) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("DELETE FROM ap_invoices_all")


def _interface_invoice(conn, *, num, vendor_id, site, amount, lines, po=None, terms=None, group="T1", key=None):
    inv_id = conn.execute("SELECT nextval('ap_invoices_interface_s')").fetchone()[0]
    conn.execute("""INSERT INTO ap_invoices_interface (invoice_id, invoice_num, invoice_type_lookup_code, invoice_date,
                        vendor_id, vendor_site_code, invoice_amount, invoice_currency_code, terms_name, po_number,
                        source, group_id, org_id, attribute15)
                    VALUES (%s, %s, 'STANDARD', DATE '2026-09-20', %s, %s, %s, 'USD', %s, %s, 'AAP', %s, 204, %s)""",
                 (inv_id, num, vendor_id, site, amount, terms, po, group, key))
    for n, (ltype, amt, qty, price, po_line, dist) in enumerate(lines, start=1):
        conn.execute("""INSERT INTO ap_invoice_lines_interface (invoice_id, invoice_line_id, line_number,
                            line_type_lookup_code, amount, quantity_invoiced, unit_price, po_number, po_line_number,
                            dist_code_concatenated, org_id)
                        VALUES (%s, nextval('ap_invoice_lines_interface_s'), %s, %s, %s, %s, %s, %s, %s, %s, 204)""",
                     (inv_id, n, ltype, amt, qty, price, po if po_line else None, po_line, dist))
    return inv_id


def test_import_program_creates_rejects_and_holds(seeded_ebs, env):
    with psycopg.connect(env["EBS_RO_DSN"], row_factory=dict_row) as ro:
        contoso = ro.execute("SELECT vendor_id FROM ap_suppliers WHERE vendor_name = 'Contoso Office Products LLC'").fetchone()["vendor_id"]
        aws = ro.execute("SELECT vendor_id FROM ap_suppliers WHERE vendor_name = 'Amazon Web Services, Inc.'").fetchone()["vendor_id"]

    with psycopg.connect(env["EBS_INTF_DSN"], autocommit=True) as conn:
        ok = _interface_invoice(conn, num="IMP-OK-1", vendor_id=30418, site="AUSTIN", amount=900, po="4500188",
                                lines=[("ITEM", 900, 5, 180, 2, None)], key="doc-1:1")
        hold = _interface_invoice(conn, num="IMP-HOLD-1", vendor_id=30418, site="DALLAS-HQ", amount=11500,
                                  po="4500123", lines=[("ITEM", 11500, 10, 1150, 1, None)])
        dup = _interface_invoice(conn, num="CT-88412", vendor_id=contoso, site="CHICAGO", amount=10,
                                 lines=[("ITEM", 10, None, None, None, "01-110-6210-0000")])
        bad = _interface_invoice(conn, num="IMP-BAD-1", vendor_id=aws, site="SEATTLE", amount=100, terms="NO SUCH TERMS",
                                 lines=[("ITEM", 90, None, None, None, "01-510-6420-0000")])
        nonpo = _interface_invoice(conn, num="IMP-NONPO-1", vendor_id=aws, site="SEATTLE", amount=100,
                                   lines=[("ITEM", 100, None, None, None, "01-510-6420-0000")])
        req = conn.execute("SELECT fnd_request_submit_request('SQLAP', 'APXIIMPT', NULL, NULL, false, "
                           "'204', 'AAP', 'T1')").fetchone()[0]
        assert req > 0

    assert run_pending(env["EBS_OWNER_DSN"]) >= 1

    with psycopg.connect(env["EBS_OWNER_DSN"], row_factory=dict_row) as conn:
        r = conn.execute("SELECT phase_code, status_code FROM fnd.fnd_concurrent_requests WHERE request_id = %s",
                         (req,)).fetchone()
        assert r == {"phase_code": "C", "status_code": "G"}   # completed with warnings (rejections)
        status = {row["invoice_id"]: row["status"] for row in conn.execute(
            "SELECT invoice_id, status FROM ap.ap_invoices_interface WHERE group_id = 'T1'")}
        assert status[ok] == status[hold] == status[nonpo] == "PROCESSED"
        assert status[dup] == status[bad] == "REJECTED"
        rejects = {(row["parent_id"], row["reject_lookup_code"]) for row in conn.execute(
            "SELECT parent_id, reject_lookup_code FROM ap.ap_interface_rejections WHERE request_id = %s", (req,))}
        assert (dup, "DUPLICATE INVOICE NUMBER") in rejects
        assert (bad, "INVALID TERMS") in rejects
        assert (bad, "LINES AMOUNT MISMATCH") in rejects

        inv = conn.execute("SELECT * FROM ap.ap_invoices_all WHERE invoice_num = 'IMP-OK-1'").fetchone()
        assert inv["attribute15"] == "doc-1:1" and inv["approval_status"] == "APPROVED"
        dist = conn.execute("""SELECT d.dist_code_combination_id, pd.code_combination_id
                               FROM ap.ap_invoice_distributions_all d
                               JOIN po.po_distributions_all pd ON pd.po_distribution_id = d.po_distribution_id
                               WHERE d.invoice_id = %s""", (inv["invoice_id"],)).fetchone()
        assert dist["dist_code_combination_id"] == dist["code_combination_id"]   # PO account copied

        held = conn.execute("""SELECT h.hold_lookup_code FROM ap.ap_holds_all h
                               JOIN ap.ap_invoices_all i ON i.invoice_id = h.invoice_id
                               WHERE i.invoice_num = 'IMP-HOLD-1'""").fetchall()
        assert {h["hold_lookup_code"] for h in held} == {"QTY REC"}              # billed 10, received 8


def test_import_program_matches_blanket_release_shipments(seeded_ebs, env):
    with psycopg.connect(env["EBS_RO_DSN"], row_factory=dict_row) as ro:
        r = ro.execute("""SELECT h.segment1, h.vendor_id, s.vendor_site_code, h.currency_code, r.release_num,
                                 r.po_release_id, l.line_num, ll.price_override
                          FROM po_headers_all h
                          JOIN po_releases_all r ON r.po_header_id = h.po_header_id
                          JOIN po_line_locations_all ll ON ll.po_release_id = r.po_release_id
                          JOIN po_lines_all l ON l.po_line_id = ll.po_line_id
                          JOIN ap_supplier_sites_all s ON s.vendor_site_id = h.vendor_site_id
                          WHERE h.type_lookup_code = 'BLANKET' AND h.org_id = 204 AND s.inactive_date IS NULL
                            AND (ll.receipt_required_flag = 'N' OR ll.quantity_received >= 1)
                          ORDER BY h.segment1, r.release_num LIMIT 1""").fetchone()
    assert r is not None
    price = r["price_override"]

    def invoice(conn, num, release):
        inv_id = conn.execute("SELECT nextval('ap_invoices_interface_s')").fetchone()[0]
        conn.execute("""INSERT INTO ap_invoices_interface (invoice_id, invoice_num, invoice_type_lookup_code,
                            invoice_date, vendor_id, vendor_site_code, invoice_amount, invoice_currency_code,
                            source, group_id, org_id)
                        VALUES (%s, %s, 'STANDARD', DATE '2026-09-20', %s, %s, %s, %s, 'AAP', 'T-REL', 204)""",
                     (inv_id, num, r["vendor_id"], r["vendor_site_code"], price, r["currency_code"]))
        conn.execute("""INSERT INTO ap_invoice_lines_interface (invoice_id, invoice_line_id, line_number,
                            line_type_lookup_code, amount, quantity_invoiced, unit_price, po_number, release_num,
                            po_line_number, org_id)
                        VALUES (%s, nextval('ap_invoice_lines_interface_s'), 1, 'ITEM', %s, 1, %s, %s, %s, %s, 204)""",
                     (inv_id, price, price, r["segment1"], release, r["line_num"]))
        return inv_id

    with psycopg.connect(env["EBS_INTF_DSN"], autocommit=True) as conn:
        ok = invoice(conn, "IMP-REL-1", r["release_num"])
        no_release = invoice(conn, "IMP-REL-2", None)
        conn.execute("SELECT fnd_request_submit_request('SQLAP', 'APXIIMPT', NULL, NULL, false, '204', 'AAP', 'T-REL')")
    run_pending(env["EBS_OWNER_DSN"])

    with psycopg.connect(env["EBS_OWNER_DSN"], row_factory=dict_row) as conn:
        status = {row["invoice_id"]: row["status"] for row in conn.execute(
            "SELECT invoice_id, status FROM ap.ap_invoices_interface WHERE group_id = 'T-REL'")}
        assert (status[ok], status[no_release]) == ("PROCESSED", "REJECTED")
        rejects = {row["reject_lookup_code"] for row in conn.execute(
            """SELECT reject_lookup_code FROM ap.ap_interface_rejections r
               JOIN ap.ap_invoice_lines_interface l ON l.invoice_line_id = r.parent_id
               WHERE r.parent_table = 'AP_INVOICE_LINES_INTERFACE' AND l.invoice_id = %s""", (no_release,))}
        assert "INVALID RELEASE NUM" in rejects
        line = conn.execute("""SELECT l.po_release_id, ll.po_release_id AS shipment_release
                               FROM ap.ap_invoice_lines_all l
                               JOIN ap.ap_invoices_all i ON i.invoice_id = l.invoice_id
                               JOIN po.po_line_locations_all ll ON ll.line_location_id = l.po_line_location_id
                               WHERE i.invoice_num = 'IMP-REL-1'""").fetchone()
        assert line["po_release_id"] == line["shipment_release"] == r["po_release_id"]
