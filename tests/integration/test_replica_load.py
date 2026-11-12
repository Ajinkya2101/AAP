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
from aap.db.models import GlHistoryLine, RefCcid, RefLookup, RefPaymentMethod, RefSupplier, RefTerms, ValueMap

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
        # the delivered lookups replace the seeded ones of the same type (P3.3); other types stay seeded
        types = {(x.lookup_type, x.lookup_code): x.enabled for x in s.scalars(select(RefLookup))}
        assert types[("INVOICE TYPE", "DEBIT")] is False and ("INVOICE TYPE", "MIXED") not in types
        assert types[("SOURCE", "AAP")] and types[("INVOICE LINE TYPE", "FREIGHT")]
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
    from tests.conftest import _reset_caches

    from aap.clientdata.replica import run
    from aap.eval.harness import run_eval
    from aap.eval.report import render

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
    # V-LOV (P3.3): every invoice's type, line types and source are in the delivered lists
    from aap.db.models import ValidationResult
    from aap.db.session import session_scope

    with session_scope(replica_env["AAP_OWNER_DSN"]) as s:
        lov = [v.result for v in s.scalars(select(ValidationResult).where(ValidationResult.rule_id == "V-LOV"))]
    assert lov and set(lov) == {"pass"}, lov
    # the held-out supplier has no history: its lines get no account and go to review, never coded wrongly
    assert gl and gl["invoices"] == sh["invoices"] and gl["no_review_accuracy_full"] == 1.0, text
    held = m["by_supplier"]["70003"]
    assert m["holdout"]["suppliers"] == ["70003"] and held["gl"]["no_review_share"] == 0.0, text
    assert all(b["shadow"]["equal"] == 1.0 for v, b in m["by_supplier"].items() if v != "70003"), text
    assert "Per supplier" in text and "GL coding vs the keyed distributions" in text


def test_context_pack_from_the_replica_matches_the_delivery(replica_env, cluster, tmp_path):
    """PLAN-MODEL-JSON E2: a context pack built in production (aap_kk: reference copy and gl_history_line
    with site, invoice and GL date) equals the one built from the delivery the replica came from, and a
    distribution set used by the lines is shown by its ID."""
    from aap.clientdata.fixtures import SET_SUPPLIER, build_list_format
    from aap.clientdata.readers import read_delivery
    from aap.clientdata.replica import run
    from aap.db.session import session_scope
    from aap.decide.context import build_context_pack, leaks
    from aap.decide.history import from_db, from_delivery

    delivery = build_list_format(tmp_path / "2026-11-09")
    result = run([delivery], create=True, admin_dsn=cluster.dsn("postgres", "postgres"), log=lambda *_: None)
    # the 2026-11-09 layout has no liability account on the sites (as the real extract)
    assert set(result["invariant_failures"]) <= {"site liability CCIDs exist"}, result["invariant_failures"]
    as_of = date(2025, 3, 1)
    offline = from_delivery(read_delivery(delivery), KK)
    with session_scope(replica_env["AAP_OWNER_DSN"]) as s:
        assert s.scalar(select(func.count()).select_from(GlHistoryLine)
                        .where(GlHistoryLine.gl_date.is_(None) | GlHistoryLine.vendor_site_id.is_(None))) == 0
        for vendor, site in ((5001, 6001), (SET_SUPPLIER, 6002)):
            online = build_context_pack(from_db(s, vendor, as_of=as_of), vendor_id=vendor, vendor_site_id=site,
                                        as_of=as_of)
            expected = build_context_pack(offline, vendor_id=vendor, vendor_site_id=site, as_of=as_of)
            assert leaks(online, as_of=as_of) == []
            for part in ("history_scope", "fallback", "accounts", "recent_invoices"):
                assert online[part] == expected[part], part
            assert [x["keyed_accounts"] for x in online["distribution_sets"]] == \
                   [x["keyed_accounts"] for x in expected["distribution_sets"]]
            assert online["site"]["terms"] == expected["site"]["terms"]
        sets = build_context_pack(from_db(s, SET_SUPPLIER, as_of=as_of), vendor_id=SET_SUPPLIER,
                                  vendor_site_id=6002, as_of=as_of)["distribution_sets"]
        assert sets and sets[0]["name"].startswith("set ")


def test_systems_s0_and_s1_on_the_same_split(replica_env, cluster, tmp_path, monkeypatch):
    """PLAN-MODEL-JSON E4: the harness runs today's pipeline (s0) and the model with the context pack (s1,
    stub backend: site defaults and the supplier's most used account) on the same split. s1's answers are
    compared with the code; every line where they differ goes to review, so nothing is coded wrongly
    without a person (the checks stay). Each run writes one run-log line."""
    from tests.conftest import _reset_caches

    from aap.clientdata.replica import run
    from aap.eval.harness import run_eval

    delivery = build(tmp_path / "2026-11-10")
    gold = tmp_path / "gold"
    build_goldset(delivery, gold, holdout={"70003"}, coa=KK, pdf_scan=False)
    _printed_values(gold, delivery)
    run([delivery], exclude=gold / "history_exclude.json", create=True,
        admin_dsn=cluster.dsn("postgres", "postgres"), log=lambda *_: None)
    monkeypatch.setenv("TRUTH_DIR", str(tmp_path / "truth"))
    monkeypatch.setenv("FILESTORE_DIR", str(tmp_path / "filestore"))
    _reset_caches()
    log = tmp_path / "runs.jsonl"
    try:
        _, m0 = run_eval(gold, name="s0", split="test", system="s0", run_log=log, log=lambda *_: None)
        _, m1 = run_eval(gold, name="s1", split="test", system="s1", run_log=log, log=lambda *_: None)
    finally:
        monkeypatch.undo()
        _reset_caches()
    assert m0["all"]["decide"] is None and m0["system"] == "s0"
    d = m1["all"]["decide"]
    # only invoices that reach GL coding are decided (the held-out MXN supplier fails validation first)
    assert d and 0 < d["invoices"] <= m1["all"]["documents"] and d["lines"] > 0, d
    assert d["agree_lines"] < 1.0                            # the stub's single account misses a store's lines
    assert m1["all"]["gl"]["no_review_accuracy_full"] in (None, 1.0), m1["all"]["gl"]
    records = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert [r["system"] for r in records] == ["s0", "s1"]
    assert records[1]["decide_mode"] == "apply" and records[0]["dataset_sha256"] == records[1]["dataset_sha256"]
    assert "70003" not in json.dumps(records)                # no supplier numbers in the run log


def test_train_split_labels_upload_and_compare(replica_env, cluster, tmp_path, monkeypatch):
    """CLIENT-DATA P6.1: the train split is uploaded with source kk-train, read (stub: the printed values),
    and compared with the keyed record; a misread total is listed for review, the rest is accepted."""
    from tests.conftest import _reset_caches

    from aap.clientdata.labels import compare, upload
    from aap.clientdata.replica import run

    delivery = build(tmp_path / "2026-11-10")
    gold = tmp_path / "gold"
    build_goldset(delivery, gold, holdout={"70003"}, coa=KK, pdf_scan=False)
    _printed_values(gold, delivery)
    run([delivery], exclude=gold / "history_exclude.json", create=True,
        admin_dsn=cluster.dsn("postgres", "postgres"), log=lambda *_: None)
    truth_dir = tmp_path / "truth"
    truth_dir.mkdir()
    misread = None
    for p in (gold / "truth").glob("*.truth.json"):          # what the stub "reads": one total misread
        t = json.loads(p.read_text(encoding="utf-8"))
        if misread is None and t["split"] == "train":
            misread = t["invoice_id"]
            t["source"]["total"] = str(Decimal(t["source"]["total"]) + 100)
        (truth_dir / p.name).write_text(json.dumps(t), encoding="utf-8")
    monkeypatch.setenv("TRUTH_DIR", str(truth_dir))
    monkeypatch.setenv("FILESTORE_DIR", str(tmp_path / "filestore"))
    _reset_caches()
    try:
        counts = upload(gold, split="train", inline=True, log=lambda *_: None)
        assert upload(gold, split="train", inline=True, log=lambda *_: None) == {"already uploaded": counts["new"]}
        results = compare(gold, split="train")
    finally:
        monkeypatch.undo()
        _reset_caches()
    assert len(results) == counts["new"] > 1
    by_id = {r.invoice_id: r for r in results}
    assert by_id[misread].status == "review" and [d["field"] for d in by_id[misread].differences] == ["total"]
    assert all(r.status == "accepted" for i, r in by_id.items() if i != misread), [
        (r.invoice_id, r.differences, r.lines_match) for r in results if r.status != "accepted"]
