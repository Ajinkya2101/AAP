"""Client replica (aap.clientdata.replica) from the made-up fixture delivery: separate databases,
8-segment chart, history without the gold set's test / dev invoices, reference sync into the
replica's aap database, and an invoice through the mock import with a "."-joined account."""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row
from sqlalchemy import func, select

from aap.clientdata.fixtures import build
from aap.clientdata.goldset import build_goldset
from aap.common.coa import CoaLayout
from aap.db.models import GlHistoryLine, RefCcid, RefPaymentMethod, RefSupplier, RefTerms, ValueMap

pytestmark = pytest.mark.integration
KK = CoaLayout(segment_count=8, separator=".", company_segment=1, cost_center_segment=3, natural_account_segment=4)
ACCOUNT = "10.20.00110.61210.00000.000.00.000"


@pytest.fixture
def replica_env(cluster):
    from tests.conftest import _reset_caches

    values = {"AAP_DSN": cluster.dsn("aap_app", "aap_kk_test"), "AAP_OWNER_DSN": cluster.dsn("aap_owner", "aap_kk_test"),
              "EBS_OWNER_DSN": cluster.dsn("ebs_owner", "ebs_kk_test"), "EBS_RO_DSN": cluster.dsn("apps_ro", "ebs_kk_test"),
              "EBS_INTF_DSN": cluster.dsn("apps_intf", "ebs_kk_test"), "GL_SEGMENT_COUNT": "8",
              "GL_SEGMENT_SEPARATOR": ".", "GL_COST_CENTER_SEGMENT": "3", "GL_NATURAL_ACCOUNT_SEGMENT": "4",
              "LLM_BACKEND": "stub", "EMBED_ENABLED": "false"}
    old = {k: os.environ.get(k) for k in values}
    os.environ.update(values)
    _reset_caches()
    yield values
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    _reset_caches()


def test_replica_from_the_fixture_delivery(replica_env, cluster, tmp_path):
    from aap.adapters.ebs_mock import MockEbsAdapter
    from aap.adapters.payload import build_payload
    from aap.clientdata.replica import run
    from aap.db.session import session_scope
    from aap.ebs_mock.import_program import run_import
    from aap.schemas.canonical import CanonicalInvoice, CanonicalLine
    from aap.schemas.fields import Field

    delivery = build(tmp_path / "2026-11-10")
    gold = tmp_path / "gold"
    build_goldset(delivery, gold, holdout={"70003"}, coa=KK, pdf_scan=False)
    exclude = json.loads((gold / "history_exclude.json").read_text(encoding="utf-8"))

    result = run([delivery], exclude=gold / "history_exclude.json", create=True,
                 admin_dsn=cluster.dsn("postgres", "postgres"), log=lambda *_: None)
    assert not result["invariant_failures"], result["invariant_failures"]
    counts = result["counts"]
    assert counts["ap.ap_suppliers"] == 3 and counts["gl.gl_code_combinations"] == 7
    assert counts["ap.ap_terms_tl"] == 5 and counts["iby.iby_payment_methods_b"] == 3
    loaded = counts["ap.ap_invoices_all"]
    assert 0 < loaded < 14                                   # test / dev and the held-out supplier are out

    # the synthetic databases are not touched: the replica has its own
    with psycopg.connect(replica_env["EBS_OWNER_DSN"], row_factory=dict_row) as conn:
        ids = {r["invoice_id"] for r in conn.execute("SELECT invoice_id FROM ap.ap_invoices_all")}
        assert not ids & set(exclude["invoice_ids"])
        assert not conn.execute("SELECT 1 FROM ap.ap_invoices_all WHERE vendor_id = 5003").fetchall()
        kfv = conn.execute("SELECT concatenated_segments FROM apps.gl_code_combinations_kfv "
                           "WHERE segment4 = '61210' AND segment3 = '00110'").fetchone()
        assert kfv["concatenated_segments"] == ACCOUNT

    with session_scope(replica_env["AAP_OWNER_DSN"]) as s:
        assert s.scalar(select(func.count()).select_from(RefSupplier)) == 3
        ccid = s.scalar(select(RefCcid).where(RefCcid.concatenated == ACCOUNT))
        assert ccid.natural_account == "61210" and len(ccid.segments) == 8 and ccid.account_desc == "Packaging supplies"
        assert s.get(RefTerms, 103).due_day_of_month == 15 and s.get(RefPaymentMethod, "WIRE") is not None
        assert s.scalar(select(func.count()).select_from(ValueMap).where(ValueMap.map_type == "location")) == 3
        history = list(s.scalars(select(GlHistoryLine)))
        assert history and all(h.natural_account and "." in h.concatenated for h in history)
        assert all(h.vendor_id != 5003 for h in history)
        assert s.scalar(select(func.count()).select_from(GlHistoryLine).where(GlHistoryLine.amount < 0)) == 0

    adapter = MockEbsAdapter(replica_env["EBS_RO_DSN"], replica_env["EBS_INTF_DSN"], coa=KK)
    try:
        assert adapter.ccid_valid(ACCOUNT, date(2025, 2, 1)).valid
        inv = CanonicalInvoice()
        for k, v in dict(invoice_num="NEW-1", invoice_date=date.today(), gl_date=date.today(), vendor_id=5001,
                         vendor_number="70001", vendor_site_id=6001, vendor_site_code="NWP-MAIN", org_id=81,
                         currency_code="USD", invoice_amount=Decimal("100.00"), terms_id=102,
                         payment_method="EFT").items():
            setattr(inv, k, Field(value=v))
        inv.lines = [CanonicalLine(line_number=1, description=Field(value="Donut boxes"),
                                   amount=Field(value=Decimal("100.00")), dist_account=Field(value=ACCOUNT))]
        ref = adapter.submit(build_payload(inv, source="AAP"), "replica-test-1")
    finally:
        adapter.close()
    with psycopg.connect(replica_env["EBS_OWNER_DSN"]) as conn:
        out = run_import(conn, group_id=ref.group_id)
        conn.commit()
        assert out.processed and not out.rejected, out
        row = conn.execute("SELECT d.dist_code_combination_id, i.payment_method_code FROM ap.ap_invoices_all i "
                           "JOIN ap.ap_invoice_distributions_all d ON d.invoice_id = i.invoice_id "
                           "WHERE i.invoice_num = 'NEW-1'").fetchone()
        assert row[0] == ccid.ccid and row[1] == "EFT"


def _printed_values(gold, delivery):
    """Gold-set truths only hold what AP keyed; give the stub backend the printed values it would
    read from these made-up pages (supplier name, dates, item lines) so the whole pipeline runs."""
    from aap.clientdata.readers import read_delivery

    names = {int(s["VENDOR_ID"]): s["VENDOR_NAME"] for s in read_delivery(delivery).rows("AP_SUPPLIERS")}
    for p in (gold / "truth").glob("*.truth.json"):
        t = json.loads(p.read_text(encoding="utf-8"))
        e = t["expected"]
        items = [ln for ln in e["lines"] if ln["line_type"] == "ITEM"]
        tax, freight = (sum(Decimal(ln["amount"]) for ln in e["lines"] if ln["line_type"] == k) for k in ("TAX", "FREIGHT"))
        t["source"].update(vendor_name=names[e["vendor_id"]], invoice_date=e["invoice_date"],
                           currency=e["currency_code"], tax_amount=str(tax) if tax else None,
                           freight_amount=str(freight) if freight else None,
                           lines=[{"description": ln["description"], "amount": ln["amount"]} for ln in items])
        p.write_text(json.dumps(t), encoding="utf-8")


def test_eval_with_agents_compares_the_json_with_the_keyed_record(replica_env, cluster, tmp_path, monkeypatch):
    """P4.2 / P8.1 / D4 end to end: gold set -> pipeline + agents on the replica -> Oracle-ready JSON
    -> shadow comparison, GL metrics, per-supplier and held-out blocks."""
    from aap.clientdata.replica import run
    from aap.eval.harness import run_eval
    from aap.eval.report import render
    from tests.conftest import _reset_caches

    delivery = build(tmp_path / "2026-11-10")
    gold = tmp_path / "gold"
    build_goldset(delivery, gold, holdout={"70003"}, coa=KK, pdf_scan=False)
    _printed_values(gold, delivery)
    run([delivery], exclude=gold / "history_exclude.json", create=True,
        admin_dsn=cluster.dsn("postgres", "postgres"), log=lambda *_: None)
    monkeypatch.setenv("TRUTH_DIR", str(tmp_path / "truth"))
    monkeypatch.setenv("FILESTORE_DIR", str(tmp_path / "filestore"))
    _reset_caches()
    try:
        run_id, m = run_eval(gold, name="replica-shadow", split="test", agents=True, log=lambda *_: None)
    finally:
        monkeypatch.undo()
        _reset_caches()
    text = render(run_id, "replica-shadow", m)
    sh, gl = m["all"]["shadow"], m["all"]["gl"]
    assert sh and sh["invoices"] == m["all"]["documents"] >= 4, text
    assert all(sh["fields"][k] == 1.0 for k in ("supplier", "site", "invoice_amount", "terms", "line_types",
                                                "amount_by_line_type")), text
    assert m["all"]["fields"]["lines.line_types"] == 1.0, text
    # the held-out supplier has no history: its lines get no account and go to review, never coded wrongly
    assert gl and gl["invoices"] == sh["invoices"] and gl["no_review_accuracy_full"] == 1.0, text
    held = m["by_supplier"]["70003"]
    assert m["holdout"]["suppliers"] == ["70003"] and held["gl"]["no_review_share"] == 0.0, text
    assert all(b["shadow"]["equal"] == 1.0 for v, b in m["by_supplier"].items() if v != "70003"), text
    assert "Per supplier" in text and "GL coding vs the keyed distributions" in text
