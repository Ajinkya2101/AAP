"""Labels for fine-tuning: a clerk's correction of the reading runs the invoice again and is
kept next to the model's reading; an approved model mapping becomes a value-map proposal;
both are exported by aap.finetune.export.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


@pytest.fixture(scope="module")
def s2(clean_aap, tmp_path_factory):
    """Scenario S2 (non-PO invoice) through the whole pipeline: waits for a clerk."""
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint system libraries not available (run the e2e tests in the app container)")
    from tools.invoicegen.__main__ import generate

    from aap.db.session import session_scope
    from aap.ingest.intake import intake
    from aap.pipeline.stages import run_pipeline_inline

    out = tmp_path_factory.mktemp("corrections")
    manifest = generate(out, count=0, seed=11, scenarios=True, install_truth=True, dsn=clean_aap["EBS_RO_DSN"])
    item = next(i for i in manifest["items"] if i["documents"] and i["documents"][0]["expected"].get("scenario") == "S2")
    with session_scope() as s:
        doc_id = intake(s, (out / item["file"]).read_bytes(), filename=Path(item["file"]).name,
                        source="upload", uploaded_by="e2e").document.id
    return run_pipeline_inline(doc_id)[-1]


def test_correction_reruns_the_invoice_and_keeps_both_readings(s2):
    from aap.db.models import Document, ExtractionRun, ReviewTask
    from aap.db.session import session_scope
    from aap.pipeline.stages import document_summary, run_pipeline_inline_from
    from aap.submit.correct import correct_extraction, latest_extraction
    from aap.submit.decision import DecisionError

    with session_scope() as s:
        doc = s.get(Document, s2)
        assert doc.status == "IN_REVIEW"
        read = latest_extraction(s, s2).source_invoice["invoice_number"]["value"]
        with pytest.raises(DecisionError, match="nothing was changed"):
            correct_extraction(s, doc, by="clerk", header={"invoice_number": read})
        run = correct_extraction(s, doc, by="clerk", header={"invoice_number": read + "-X"}, note="misread")
        assert doc.status == "EXTRACTED"
        assert run.backend == "user" and run.raw_output[0]["changes"] == [
            {"path": "invoice_number", "model": read, "corrected": read + "-X"}]
    run_pipeline_inline_from(s2, "normalize")
    with session_scope() as s:
        summary = document_summary(s, s2)
        assert summary["status"] == "IN_REVIEW"
        assert summary["invoice"]["invoice_num"]["value"] == read + "-X"
        runs = s.scalars(select(ExtractionRun).where(ExtractionRun.document_id == s2)
                         .order_by(ExtractionRun.created_at)).all()
        assert [r.backend for r in runs] == ["stub", "user"]
        assert runs[0].source_invoice["invoice_number"]["value"] == read      # the model's reading is kept
        open_tasks = s.scalars(select(ReviewTask).where(ReviewTask.document_id == s2,
                                                        ReviewTask.status == "open")).all()
        assert len(open_tasks) == 1


def test_correction_api_and_form(s2):
    from fastapi.testclient import TestClient

    from aap.api.main import app

    client = TestClient(app)
    r = client.post(f"/api/documents/{s2}/corrections?inline=true",
                    json={"by": "api", "header": {"payment_terms": "Net 45"}})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "IN_REVIEW" and r.json()["changes"][0]["corrected"] == "Net 45"
    assert client.post(f"/api/documents/{s2}/corrections", json={"header": {"payment_terms": "Net 45"}}).status_code == 409
    page = client.get(f"/ui/documents/{s2}")
    assert page.status_code == 200 and 'name="h_payment_terms" value="Net 45"' in page.text


def test_export_writes_the_corrected_invoice(s2, tmp_path, monkeypatch):
    from aap.finetune import export as ex

    monkeypatch.setattr(ex, "REAL_BACKEND", "stub")      # the tests extract with the stub
    summary = ex.export(tmp_path)
    assert summary["extraction"] == {"corrected": 1}
    row = json.loads((tmp_path / "extraction.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["target"]["payment_terms"] == "Net 45" and row["target"]["invoice_number"].endswith("-X")
    assert not row["model_output"]["invoice_number"].endswith("-X")
    assert {c["path"] for c in row["changes"]} == {"invoice_number", "payment_terms"}
    assert row["pages"][0]["image"] and (tmp_path / row["pages"][0]["image"]).exists()


def test_approved_model_mapping_becomes_a_value_map(clean_aap):
    from aap.db.models import RefSupplier, RefTerms, ValueMap, ValueMapProposal
    from aap.db.session import session_scope
    from aap.mapping.proposals import ProposalError, decide_proposal, propose_value_maps
    from aap.mapping.reference import load_reference
    from aap.schemas.canonical import CanonicalInvoice
    from aap.schemas.fields import Field, Method

    with session_scope() as s:
        supplier = s.scalars(select(RefSupplier).limit(1)).one()
        terms = s.scalars(select(RefTerms).where(RefTerms.enabled).limit(1)).one()
        inv = CanonicalInvoice()
        inv.vendor_id = Field(value=supplier.vendor_id, source_value="Nrthwnd I.T.", method=Method.fuzzy_llm)
        inv.vendor_name = Field(value=supplier.vendor_name, source_value="Nrthwnd I.T.", method=Method.fuzzy_llm)
        inv.terms_id = Field(value=terms.term_id, source_value="pay us in a month", method=Method.fuzzy_llm)
        inv.terms_name = Field(value=terms.name, source_value="pay us in a month", method=Method.fuzzy_llm)
        assert propose_value_maps(s, None, inv, "clerk") == 2
        s.flush()
        assert propose_value_maps(s, None, inv, "clerk") == 0          # already pending
        inv.vendor_id.method = inv.terms_id.method = Method.exact
        inv.vendor_id.source_value = "Another printed name"
        assert propose_value_maps(s, None, inv, "clerk") == 0          # not a model choice
        by_type = {p.map_type: p for p in s.scalars(select(ValueMapProposal))}
        assert by_type["supplier"].vendor_id is None and by_type["terms"].vendor_id == supplier.vendor_id
        decide_proposal(s, by_type["supplier"].id, by="lead", approve=True)
        decide_proposal(s, by_type["terms"].id, by="lead", approve=False)
        with pytest.raises(ProposalError):
            decide_proposal(s, by_type["terms"].id, by="lead", approve=True)
        s.flush()
        assert s.scalars(select(ValueMap).where(ValueMap.map_type == "supplier")).one().approved_by == "lead"
        ref = load_reference(s)
        assert ref.value_map("supplier", "NRTHWND I.T.").oracle_id == supplier.vendor_id
        assert ref.value_map("terms", "pay us in a month", supplier.vendor_id) is None
