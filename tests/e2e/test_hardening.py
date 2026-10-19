"""Phase 10 hardening: failure modes end in a recoverable, visible state, and the audit
trail of a full flow is complete (aap.audit_check)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.integration]


@pytest.fixture(scope="module")
def gen(clean_aap, tmp_path_factory):
    try:
        import weasyprint  # noqa: F401
    except (ImportError, OSError):
        pytest.skip("WeasyPrint system libraries not available (run the e2e tests in the app container)")
    from tools.invoicegen.__main__ import generate

    out = tmp_path_factory.mktemp("hardening")
    manifest = generate(out, count=0, seed=11, scenarios=True, install_truth=True, dsn=clean_aap["EBS_RO_DSN"])
    files = {i["documents"][0]["expected"].get("scenario"): out / i["file"] for i in manifest["items"] if i["documents"]}
    return files


def _intake(path: Path):  # noqa: ANN202
    from aap.db.session import session_scope
    from aap.ingest.intake import intake

    with session_scope() as s:
        return intake(s, path.read_bytes(), filename=path.name, source="upload", uploaded_by="e2e").document.id


def _status(doc_id) -> str:  # noqa: ANN001
    from aap.db.models import Document
    from aap.db.session import session_scope

    with session_scope() as s:
        return s.get(Document, doc_id).status


def _open_reasons(doc_id) -> set[str]:  # noqa: ANN001
    from sqlalchemy import select

    from aap.db.models import ReviewTask
    from aap.db.session import session_scope

    with session_scope() as s:
        return {r for t in s.scalars(select(ReviewTask).where(ReviewTask.document_id == doc_id,
                                                             ReviewTask.status == "open")) for r in t.reason_codes}


def test_llm_unreachable_fails_then_retry_recovers(gen):
    from aap.config import get_settings
    from aap.llm import get_llm
    from aap.pipeline.stages import run_pipeline_inline_from, run_stage

    doc_id = _intake(gen["S5"])
    for stage in ("parse", "route"):
        run_stage(stage, doc_id)
    s = get_settings()
    saved = (s.llm_backend, s.vllm_base_url, s.llm_timeout_seconds)
    s.llm_backend, s.vllm_base_url, s.llm_timeout_seconds = "vllm", "http://127.0.0.1:9/v1", 2
    get_llm.cache_clear()
    try:
        with pytest.raises(Exception):  # noqa: B017 - connection error: the job is retried
            run_stage("extract", doc_id, final_attempt=False)
        assert _status(doc_id) == "ROUTED"
        assert run_stage("extract", doc_id, final_attempt=True) == []
        assert _status(doc_id) == "FAILED"
    finally:
        s.llm_backend, s.vllm_base_url, s.llm_timeout_seconds = saved
        get_llm.cache_clear()
    # Administrator restart (API /retry, UI admin page): back to RECEIVED, whole pipeline again.
    from aap.api.main import retry
    from aap.db.session import session_scope

    with session_scope() as sess:
        retry(sess, doc_id)
    run_pipeline_inline_from(doc_id, "parse")
    assert _status(doc_id) == "ON_HOLD"          # S5: price variance, as in the normal run


def test_oracle_down_at_submit_goes_to_review_and_reapproval_posts(gen, aap_db, monkeypatch):
    from aap.db.models import Document
    from aap.db.session import session_scope
    from aap.ebs_mock.concurrent_manager import run_pending
    from aap.pipeline.stages import run_pipeline_inline, run_stage
    from aap.submit.decision import approve
    from aap.submit.submit import wait_for_result

    doc_id = run_pipeline_inline(_intake(gen["S8"]))[-1]
    with session_scope() as s:
        approve(s, s.get(Document, doc_id), by="e2e")

    def down():  # noqa: ANN202
        raise ConnectionError("EBS listener unreachable")

    monkeypatch.setattr("aap.submit.submit.get_write_adapter", down)
    with pytest.raises(ConnectionError):
        run_stage("submit", doc_id, final_attempt=False)
    assert _status(doc_id) == "APPROVED"          # retried by the queue, nothing half-done
    run_stage("submit", doc_id, final_attempt=True)
    assert _status(doc_id) == "ORACLE_ERROR" and "ORACLE_UNAVAILABLE" in _open_reasons(doc_id)
    monkeypatch.undo()

    with session_scope() as s:                    # the clerk approves again when Oracle is back
        approve(s, s.get(Document, doc_id), by="e2e", note="Oracle is back")
    assert run_stage("submit", doc_id) == [("result", doc_id)]
    assert wait_for_result(doc_id, run_import=lambda: run_pending(aap_db["EBS_OWNER_DSN"])) == "created"


def test_duplicate_jobs_are_idempotent(gen):
    from sqlalchemy import func, select

    from aap.db.models import AgentRun, Page
    from aap.db.session import session_scope
    from aap.pipeline.stages import run_pipeline_inline, run_stage

    doc_id = run_pipeline_inline(_intake(gen["S9"]))[-1]
    for stage in ("parse", "route", "extract", "normalize", "map", "validate", "agents"):
        assert run_stage(stage, doc_id) == []                   # a re-delivered job is skipped
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(Page).where(Page.document_id == doc_id)) == 1
        assert s.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.document_id == doc_id)) == 1


def test_audit_trail_complete(gen, aap_db):
    """Everything this module did (including the failures) is traceable."""
    from aap.audit_check import check
    from aap.db.session import session_scope

    with session_scope() as s:
        assert check(s) == []


def test_truncated_llm_output_is_recorded_and_not_retried(gen, monkeypatch):
    """A looping answer (finish_reason=length) fails the document at once (no job retries) and the
    raw output is kept on an error extraction_run. Regression: recording it inside the stage's
    transaction deadlocked on the document row lock."""
    from sqlalchemy import select

    from aap.db.models import ExtractionRun
    from aap.db.session import session_scope
    from aap.llm.client import LlmError
    from aap.pipeline.stages import run_stage

    doc_id = _intake(gen["S7"])
    for stage in ("parse", "route"):
        run_stage(stage, doc_id)

    def looping(*a, **k):  # noqa: ANN002, ANN003, ANN202
        raise LlmError("LLM output truncated at max_tokens=4096", retryable=False, raw='{"lines": [{"a": 1}, {"a": 1')

    monkeypatch.setattr("aap.pipeline.stages.extract", looping)
    assert run_stage("extract", doc_id, final_attempt=False) == []     # not re-raised: no job retry
    assert _status(doc_id) == "FAILED"
    with session_scope() as s:
        run = s.scalars(select(ExtractionRun).where(ExtractionRun.document_id == doc_id)).one()
        assert run.status == "error" and run.raw_output[0]["raw"].startswith('{"lines"')
