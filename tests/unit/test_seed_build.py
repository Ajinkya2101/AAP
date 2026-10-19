"""The seeder builds deterministic data in memory (no database)."""

from decimal import Decimal

from aap.ebs_mock.seed import build
from aap.ebs_mock.seed.context import TABLE_ORDER


def test_seed_is_deterministic_and_consistent():
    a = build(seed=42, scale="small")
    b = build(seed=42, scale="small")
    for table in TABLE_ORDER:
        assert len(a.rows[table]) == len(b.rows[table]), table
    assert a.rows["ap.ap_suppliers"][:5] == b.rows["ap.ap_suppliers"][:5]

    # Golden fixture S1.
    po = a.ref["pos_by_number"]["4500123"]
    assert po["supplier"]["vendor_id"] == 30418
    assert [ln["received"] for ln in po["lines"]] == [Decimal(8), Decimal(10), Decimal(1), Decimal(0)]

    # Invoice amount = sum of lines = sum of distributions.
    lines, dists = {}, {}
    for r in a.rows["ap.ap_invoice_lines_all"]:
        lines[r["invoice_id"]] = lines.get(r["invoice_id"], Decimal(0)) + r["amount"]
    for r in a.rows["ap.ap_invoice_distributions_all"]:
        dists[r["invoice_id"]] = dists.get(r["invoice_id"], Decimal(0)) + r["amount"]
    for inv in a.rows["ap.ap_invoices_all"]:
        assert lines[inv["invoice_id"]] == inv["invoice_amount"] == dists[inv["invoice_id"]]

    # Unique (vendor, invoice number).
    keys = [(r["vendor_id"], r["invoice_num"].upper()) for r in a.rows["ap.ap_invoices_all"]]
    assert len(keys) == len(set(keys))

    # S2 history: 14 EC2 lines coded 6420.
    ccids = {r["code_combination_id"]: r for r in a.rows["gl.gl_code_combinations"]}
    ec2 = [d for d in a.rows["ap.ap_invoice_distributions_all"]
           if d["description"].startswith("Amazon Elastic Compute Cloud")]
    assert len(ec2) == 14
    assert {ccids[d["dist_code_combination_id"]]["segment3"] for d in ec2} == {"6420"}
