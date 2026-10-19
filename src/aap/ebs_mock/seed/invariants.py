"""Invariants checked after seeding (plan 3.5). Seeding fails if any breaks."""

from __future__ import annotations

import psycopg

CHECKS: dict[str, str] = {
    "po shipment QUANTITY_RECEIVED = net RECEIVE + CORRECT - RETURN": """
        SELECT ll.line_location_id
        FROM po.po_line_locations_all ll
        LEFT JOIN (
            SELECT po_line_location_id,
                   SUM(CASE transaction_type
                         WHEN 'RECEIVE' THEN quantity
                         WHEN 'CORRECT' THEN quantity
                         WHEN 'RETURN TO VENDOR' THEN -quantity
                         ELSE 0 END) AS qty
            FROM po.rcv_transactions GROUP BY po_line_location_id) r
          ON r.po_line_location_id = ll.line_location_id
        WHERE coalesce(r.qty, 0) <> coalesce(ll.quantity_received, 0)""",
    "billed <= received for 3-way lines": """
        SELECT line_location_id FROM po.po_line_locations_all
        WHERE receipt_required_flag = 'Y' AND quantity_billed > quantity_received""",
    "billed <= ordered": """
        SELECT line_location_id FROM po.po_line_locations_all
        WHERE quantity_billed > quantity - coalesce(quantity_cancelled, 0) AND coalesce(cancel_flag, 'N') = 'N'""",
    "distribution billed = shipment billed": """
        SELECT ll.line_location_id FROM po.po_line_locations_all ll
        JOIN po.po_distributions_all d ON d.line_location_id = ll.line_location_id
        GROUP BY ll.line_location_id, ll.quantity_billed
        HAVING SUM(d.quantity_billed) <> ll.quantity_billed""",
    "invoice amount = sum of lines": """
        SELECT i.invoice_id FROM ap.ap_invoices_all i
        JOIN ap.ap_invoice_lines_all l ON l.invoice_id = i.invoice_id
        GROUP BY i.invoice_id, i.invoice_amount HAVING SUM(l.amount) <> i.invoice_amount""",
    "invoice amount = sum of distributions": """
        SELECT i.invoice_id FROM ap.ap_invoices_all i
        JOIN ap.ap_invoice_distributions_all d ON d.invoice_id = i.invoice_id
        GROUP BY i.invoice_id, i.invoice_amount HAVING SUM(d.amount) <> i.invoice_amount""",
    "every invoice has lines": """
        SELECT i.invoice_id FROM ap.ap_invoices_all i
        WHERE NOT EXISTS (SELECT 1 FROM ap.ap_invoice_lines_all l WHERE l.invoice_id = i.invoice_id)""",
    "distribution CCIDs exist": """
        SELECT d.invoice_distribution_id FROM ap.ap_invoice_distributions_all d
        LEFT JOIN gl.gl_code_combinations c ON c.code_combination_id = d.dist_code_combination_id
        WHERE c.code_combination_id IS NULL""",
    "PO distribution CCIDs exist": """
        SELECT d.po_distribution_id FROM po.po_distributions_all d
        LEFT JOIN gl.gl_code_combinations c ON c.code_combination_id = d.code_combination_id
        WHERE c.code_combination_id IS NULL""",
    "site liability CCIDs exist": """
        SELECT s.vendor_site_id FROM ap.ap_supplier_sites_all s
        LEFT JOIN gl.gl_code_combinations c ON c.code_combination_id = s.accts_pay_code_combination_id
        WHERE c.code_combination_id IS NULL""",
    "invoice site belongs to the supplier and the invoice OU": """
        SELECT i.invoice_id FROM ap.ap_invoices_all i
        JOIN ap.ap_supplier_sites_all s ON s.vendor_site_id = i.vendor_site_id
        WHERE s.vendor_id <> i.vendor_id OR s.org_id <> i.org_id""",
    "PO site belongs to the supplier and the PO OU": """
        SELECT h.po_header_id FROM po.po_headers_all h
        JOIN ap.ap_supplier_sites_all s ON s.vendor_site_id = h.vendor_site_id
        WHERE s.vendor_id <> h.vendor_id OR s.org_id <> h.org_id""",
    "no duplicate (VENDOR_ID, INVOICE_NUM)": """
        SELECT vendor_id FROM ap.ap_invoices_all
        GROUP BY vendor_id, upper(invoice_num) HAVING count(*) > 1""",
    "open GL periods exist for AP": """
        SELECT 1 WHERE NOT EXISTS (
            SELECT 1 FROM gl.gl_period_statuses WHERE application_id = 200 AND closing_status = 'O')""",
    "S1 fixture: PO 4500123 line 1 received 8 of 10": """
        SELECT 1 WHERE NOT EXISTS (
            SELECT 1 FROM po.po_headers_all h
            JOIN po.po_lines_all l ON l.po_header_id = h.po_header_id AND l.line_num = 1
            JOIN po.po_line_locations_all ll ON ll.po_line_id = l.po_line_id
            WHERE h.segment1 = '4500123' AND h.vendor_id = 30418
              AND ll.quantity = 10 AND ll.quantity_received = 8)""",
}


def check(conn: psycopg.Connection) -> dict[str, int]:
    """Returns {check name: number of violating rows} for the failing checks."""
    failures: dict[str, int] = {}
    for name, sql in CHECKS.items():
        rows = conn.execute(sql).fetchall()  # type: ignore[arg-type]
        if rows:
            failures[name] = len(rows)
    return failures
