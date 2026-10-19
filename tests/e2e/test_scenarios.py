"""Golden scenarios S1-S15 through the whole pipeline and the agent layer (stub LLM).

Generates the scenario invoices with tools.invoicegen (WeasyPrint), delivers each file
through intake + the in-process pipeline, and checks:
  - Phase 4: validation results and flags (aap.ebs_mock.scenarios expect_fail/warn/flags)
  - Phase 7/8: final status, review-task reasons, proposed holds
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.integration]

FINAL = {"IN_REVIEW", "ON_HOLD"}


def _deliver(out: Path, item: dict) -> list:
    from aap.db.models import Document
    from aap.db.session import session_scope
    from aap.ingest.intake import intake
    from aap.pipeline.stages import run_pipeline_inline

    data = (out / item["file"]).read_bytes()
    with session_scope() as s:
        doc_id = intake(s, data, filename=Path(item["file"]).name, source="upload", uploaded_by="e2e").document.id
    touched = run_pipeline_inline(doc_id)
    with session_scope() as s:
        return [(d.id, d.status, d.file_type, list(d.flags or [])) for d in (s.get(Document, t) for t in touched)]


@pytest.fixture(scope="module")
def delivered(clean_aap, tmp_path_factory):
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint system libraries not available (run the e2e tests in the app container)")
    from tools.invoicegen.__main__ import generate

    out = tmp_path_factory.mktemp("invoicegen")
    manifest = generate(out, count=6, seed=11, scenarios=True, install_truth=True, dsn=clean_aap["EBS_RO_DSN"])
    results = []   # (item, expected, doc tuple)
    for item in manifest["items"]:
        docs = _deliver(out, item)
        if item.get("expected", {}).get("scenario") == "S14":
            results.append((item, item["expected"], docs[0]))
            continue
        invoices = [d for d in docs if d[2] not in ("eml", "msg")]
        assert len(invoices) == len(item["documents"]), (item["file"], docs)
        for doc, d in zip(invoices, item["documents"], strict=True):
            results.append((item, d["expected"], doc))
    return results


def _agent_view(doc_id):  # noqa: ANN001
    from sqlalchemy import select

    from aap.db.models import Hold, ReviewTask
    from aap.db.session import session_scope

    with session_scope() as s:
        tasks = s.scalars(select(ReviewTask).where(ReviewTask.document_id == doc_id, ReviewTask.status == "open")).all()
        holds = s.scalars(select(Hold).where(Hold.document_id == doc_id)).all()
        return ({r for t in tasks for r in t.reason_codes}, [h.hold_lookup_code for h in holds],
                [(t.suggested_action, t.summary) for t in tasks])


def test_scenarios(delivered):
    from aap.db.session import session_scope
    from aap.pipeline.stages import document_summary

    failures: list[str] = []
    report: list[str] = []
    seen: set[str] = set()
    for item, expected, (doc_id, status, _, flags) in delivered:
        scen = expected.get("scenario") or item["file"]
        if expected.get("scenario"):
            seen.add(expected["scenario"])
        if scen == "S14":
            if status != "NOT_INVOICE":
                failures.append(f"S14: status {status}, expected NOT_INVOICE")
            continue
        with session_scope() as s:
            summary = document_summary(s, doc_id)
        want_status = expected.get("expect_status")
        if (want_status and status != want_status) or (not want_status and status not in FINAL):
            failures.append(f"{scen}: status {status} != {want_status or FINAL} ({summary.get('error')})")
        inv = summary["invoice"]
        if inv is None:
            failures.append(f"{scen}: no invoice version")
            continue
        results = summary["validation"]
        fails = {r["rule_id"] for r in results if r["result"] == "fail"}
        warns = {r["rule_id"] for r in results if r["result"] == "warning"}
        if inv["vendor_id"]["value"] != expected["vendor_id"]:
            failures.append(f"{scen}: vendor {inv['vendor_id']['value']} != {expected['vendor_id']}")
        if expected.get("vendor_site_id") and inv["vendor_site_id"]["value"] != expected["vendor_site_id"]:
            failures.append(f"{scen}: site {inv['vendor_site_id']['value']} != {expected['vendor_site_id']}")
        if expected.get("invoice_amount") and float(inv["invoice_amount"]["value"] or 0) != float(expected["invoice_amount"]):
            failures.append(f"{scen}: total {inv['invoice_amount']['value']} != {expected['invoice_amount']}")
        if expected.get("invoice_date") and inv["invoice_date"]["value"] != expected["invoice_date"]:
            failures.append(f"{scen}: date {inv['invoice_date']['value']} != {expected['invoice_date']}")
        want_fail = set(expected.get("expect_fail", []))
        if fails != want_fail:
            msgs = [f"{r['rule_id']}: {r['message']}" for r in results if r["result"] == "fail"]
            failures.append(f"{scen}: fails {sorted(fails)} != expected {sorted(want_fail)} {msgs}")
        if missing := set(expected.get("expect_warn", [])) - warns:
            failures.append(f"{scen}: missing warnings {sorted(missing)}")
        all_flags = set(flags) | set(inv.get("flags", []))
        for k in inv:
            if isinstance(inv[k], dict) and "flags" in inv[k]:
                all_flags |= set(inv[k]["flags"])
        if missing := set(expected.get("expect_flags", [])) - all_flags:
            failures.append(f"{scen}: missing flags {sorted(missing)}")

        reasons, holds, _ = _agent_view(doc_id)
        if missing := set(expected.get("expect_reasons", [])) - reasons:
            failures.append(f"{scen}: missing review reasons {sorted(missing)} (got {sorted(reasons)})")
        if sorted(holds) != sorted(expected.get("expect_holds", [])):
            failures.append(f"{scen}: holds {holds} != {expected.get('expect_holds', [])}")
        report.append(f"{str(scen)[:26]:26s} {status:10s} fail={','.join(sorted(fails)) or '-':12s} "
                      f"reasons={','.join(sorted(reasons)) or '-'} holds={','.join(holds) or '-'}")
    print("\n" + "\n".join(report))
    assert {f"S{i}" for i in range(1, 16)} <= seen, sorted({f"S{i}" for i in range(1, 16)} - seen)
    assert not failures, "\n".join(failures)


def test_vendor_problems_go_to_review(delivered):
    """No vendor email in this build: a missing PO number (S3) and wrong invoice maths (S11)
    go to a person with the request to the vendor as the suggested action."""
    for scen, action, text in (("S3", "ask_vendor_for_po_number", "Ask the vendor for the PO number"),
                               ("S11", "ask_vendor_for_corrected_invoice", "do not add up")):
        doc_id, status = next((doc[0], doc[1]) for _, exp, doc in delivered if exp.get("scenario") == scen)
        _, holds, tasks = _agent_view(doc_id)
        assert status == "IN_REVIEW" and not holds, (scen, status, holds)
        assert [a for a, _ in tasks] == [action], (scen, tasks)
        assert text in tasks[0][1], (scen, tasks)


def test_recheck_holds_after_receipt(delivered):
    """Plan 12 Phase 7: S1 is on a QTY REC hold; `aap-mock receive` brings the missing 2
    units -> recheck_holds re-runs the agents -> the hold is released, IN_REVIEW."""
    from aap.agents.recheck import recheck_holds
    from aap.db.models import Document, Hold
    from aap.db.session import session_scope
    from aap.ebs_mock.cli import main as aap_mock

    doc_id = next(doc[0] for _, exp, doc in delivered if exp.get("scenario") == "S1")
    assert recheck_holds(inline=True) == []                  # nothing changed yet
    assert aap_mock(["receive", "--po", "4500123", "--line", "1", "--qty", "2"]) in (0, None)
    assert recheck_holds(inline=True) == [str(doc_id)]
    with session_scope() as s:
        from sqlalchemy import select

        assert s.get(Document, doc_id).status == "IN_REVIEW"
        assert {h.status for h in s.scalars(select(Hold).where(Hold.document_id == doc_id))} == {"released"}
    reasons, _, tasks = _agent_view(doc_id)
    assert "QTY_VARIANCE" not in reasons, reasons
    assert any("released" in t for _, t in tasks), tasks
    assert recheck_holds(inline=True) == []


def test_audit_trail_complete_after_module():
    """aap.audit_check over everything this module did (plan 12 Phase 10)."""
    from aap.audit_check import check
    from aap.db.session import session_scope

    with session_scope() as s:
        assert check(s) == []
