"""Phase 9: approve -> stage 10 submit (open interface + APXIIMPT) -> stage 11 result.

Done when (plan 12): approved S1 -> AP_INVOICES_ALL row; re-submitting creates no
duplicate; S4 -> DUPLICATE INVOICE NUMBER. Plus: non-PO (S2, GL accounts from the
agents) and foreign currency (S8, EUR -> USD ledger) post cleanly.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest
from psycopg.rows import dict_row

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


@pytest.fixture(scope="module")
def scen(clean_aap, tmp_path_factory):
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint system libraries not available (run the e2e tests in the app container)")
    from tools.invoicegen.__main__ import generate

    from aap.db.session import session_scope
    from aap.ingest.intake import intake
    from aap.pipeline.stages import run_pipeline_inline

    out = tmp_path_factory.mktemp("submit")
    manifest = generate(out, count=0, seed=11, scenarios=True, install_truth=True, dsn=clean_aap["EBS_RO_DSN"])
    docs = {}
    for item in manifest["items"]:
        scenario = item["documents"][0]["expected"].get("scenario") if item["documents"] else None
        if scenario not in ("S1", "S2", "S4", "S8"):
            continue
        with session_scope() as s:
            doc_id = intake(s, (out / item["file"]).read_bytes(), filename=Path(item["file"]).name,
                            source="upload", uploaded_by="e2e").document.id
        docs[scenario] = run_pipeline_inline(doc_id)[-1]
    return docs


def _approve_and_post(doc_id, env, corrections=None):  # noqa: ANN001, ANN202
    from aap.db.models import Document
    from aap.db.session import session_scope
    from aap.ebs_mock.concurrent_manager import run_pending
    from aap.pipeline.stages import run_stage
    from aap.submit.decision import approve
    from aap.submit.submit import wait_for_result

    with session_scope() as s:
        approve(s, s.get(Document, doc_id), by="e2e", corrections=corrections)
    assert run_stage("submit", doc_id) == [("result", doc_id)]
    return wait_for_result(doc_id, run_import=lambda: run_pending(env["EBS_OWNER_DSN"]))


def _submission(doc_id):  # noqa: ANN001, ANN202
    from sqlalchemy import select

    from aap.db.models import InvoiceVersion, Submission
    from aap.db.session import session_scope

    with session_scope() as s:
        sub = s.scalars(select(Submission).join(InvoiceVersion, InvoiceVersion.id == Submission.invoice_version_id)
                        .where(InvoiceVersion.document_id == doc_id)).one()
        return {c: getattr(sub, c) for c in ("idempotency_key", "status", "oracle_invoice_id", "oracle_holds",
                                             "oracle_errors", "interface_invoice_id", "invoice_version_id")}


def _ebs(env, sql, *args):  # noqa: ANN001, ANN202
    with psycopg.connect(env["EBS_OWNER_DSN"], row_factory=dict_row) as conn:
        return conn.execute(sql, args).fetchall()


def test_s1_posts_with_qty_hold_and_is_idempotent(scen, aap_db):
    from aap.adapters import get_write_adapter
    from aap.db.models import Document, Hold, InvoiceVersion
    from aap.db.session import session_scope
    from aap.pipeline.stages import run_stage
    from aap.schemas.canonical import CanonicalInvoice

    doc_id = scen["S1"]
    assert _approve_and_post(doc_id, aap_db) == "created"
    sub = _submission(doc_id)
    rows = _ebs(aap_db, "SELECT invoice_id, invoice_num, invoice_amount, source FROM ap.ap_invoices_all "
                        "WHERE attribute15 = %s", sub["idempotency_key"])
    assert len(rows) == 1 and str(rows[0]["invoice_id"]) == sub["oracle_invoice_id"]
    assert rows[0]["invoice_num"] == "NW-INV-20457" and str(rows[0]["invoice_amount"]) == "14904.00"
    assert sub["oracle_holds"] == ["QTY REC"]
    with session_scope() as s:
        assert s.get(Document, doc_id).status == "CREATED"
        assert {h.status for h in s.query(Hold).filter(Hold.document_id == doc_id)} == {"active"}
        version = s.get(InvoiceVersion, sub["invoice_version_id"])
        payload_inv = CanonicalInvoice.model_validate(version.canonical_invoice)

    # Re-running the stage and re-submitting the same key create nothing new (R-07).
    assert run_stage("submit", doc_id) == []
    adapter = get_write_adapter()
    ref = adapter.submit(adapter.to_payload(payload_inv), sub["idempotency_key"])
    assert ref.interface_invoice_id == sub["interface_invoice_id"]
    assert len(_ebs(aap_db, "SELECT 1 FROM ap.ap_invoices_all WHERE invoice_num = 'NW-INV-20457'")) == 1
    assert len(_ebs(aap_db, "SELECT 1 FROM ap.ap_invoices_interface WHERE attribute15 = %s",
                    sub["idempotency_key"])) == 1


def test_s4_duplicate_is_rejected_by_oracle(scen, aap_db):
    from aap.db.models import Document, ReviewTask
    from aap.db.session import session_scope
    from aap.submit.decision import DecisionError, approve

    doc_id = scen["S4"]
    with session_scope() as s, pytest.raises(DecisionError, match="neither a PO line nor a GL account"):
        approve(s, s.get(Document, doc_id), by="e2e")
    # Triage sent it straight to review (C10), so no agent pairing: the clerk pairs the lines.
    lines = {"1": {"po_line_number": 1}, "2": {"po_line_number": 2}}
    assert _approve_and_post(doc_id, aap_db, {"lines": lines}) == "error"
    sub = _submission(doc_id)
    assert "DUPLICATE INVOICE NUMBER" in sub["oracle_errors"]
    with session_scope() as s:
        assert s.get(Document, doc_id).status == "ORACLE_ERROR"
        task = s.query(ReviewTask).filter(ReviewTask.document_id == doc_id, ReviewTask.status == "open").one()
        assert "DUPLICATE INVOICE NUMBER" in task.reason_codes and task.suggested_action == "fix_and_resubmit"


def test_s2_non_po_uses_agent_gl_accounts(scen, aap_db):
    doc_id = scen["S2"]
    assert _approve_and_post(doc_id, aap_db) == "created"
    sub = _submission(doc_id)
    accounts = [r["acct"] for r in _ebs(aap_db, """
        SELECT k.concatenated_segments AS acct FROM ap.ap_invoice_distributions_all d
        JOIN apps.gl_code_combinations_kfv k ON k.code_combination_id = d.dist_code_combination_id
        WHERE d.invoice_id = %s AND d.line_type_lookup_code = 'ITEM' ORDER BY d.invoice_line_number""",
        int(sub["oracle_invoice_id"]))]
    assert accounts and accounts[0].split("-")[2] == "6420", accounts     # EC2 -> 6420 from history

    # Feedback (plan 8): the line the model coded (clerk approved it) is now exact history.
    from sqlalchemy import select

    from aap.agents.tools.gl import exact_history
    from aap.db.models import GlHistoryLine, InvoiceVersion
    from aap.db.session import session_scope

    with session_scope() as s:
        learned = s.scalars(select(GlHistoryLine).where(GlHistoryLine.source == "review")).all()
        assert len(learned) == 1, [(r.description, r.concatenated) for r in learned]
        row = learned[0]
        assert exact_history(s, row.vendor_id, row.description)
        approved = s.get(InvoiceVersion, sub["invoice_version_id"]).canonical_invoice
        assert row.concatenated == approved["lines"][1]["dist_account"]["value"]


def test_s8_foreign_currency(scen, aap_db):
    doc_id = scen["S8"]
    assert _approve_and_post(doc_id, aap_db) == "created"
    sub = _submission(doc_id)
    row = _ebs(aap_db, "SELECT invoice_currency_code, exchange_rate, exchange_rate_type FROM ap.ap_invoices_all "
                       "WHERE invoice_id = %s", int(sub["oracle_invoice_id"]))[0]
    assert row["invoice_currency_code"] == "EUR" and row["exchange_rate"] and row["exchange_rate_type"] == "Corporate"


def test_audit_trail_complete_after_module():
    """aap.audit_check over everything this module did (plan 12 Phase 10)."""
    from aap.audit_check import check
    from aap.db.session import session_scope

    with session_scope() as s:
        assert check(s) == []
