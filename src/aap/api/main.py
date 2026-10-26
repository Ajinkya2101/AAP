"""FastAPI app. The API adds jobs; it never calls the LLM or Oracle itself (ARCHITECTURE 4),
except in `?inline=true` dev mode, which runs the pipeline in the request."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from aap import __version__
from aap.config import get_settings
from aap.db.models import AgentDecision, AgentRun, Document, Hold, ReviewTask
from aap.db.session import get_engine, session_scope
from aap.filestore import FileStore
from aap.ingest.intake import IntakeError, intake
from aap.logging import configure_logging
from aap.pipeline.stages import document_summary, run_pipeline_inline, run_pipeline_inline_from

configure_logging(get_settings().log_level)
app = FastAPI(title="ACSE Autonomous AP", version=__version__,
              description="Invoice pipeline, agent layer and mock Oracle EBS (dev build, no auth).")


def db() -> Session:  # FastAPI dependency
    with session_scope() as session:
        yield session  # type: ignore[misc]


DB = Annotated[Session, Depends(db)]


# ------------------------------------------------------------------ health
@app.get("/health", tags=["ops"])
def health() -> dict[str, Any]:
    s = get_settings()
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"database unavailable: {exc}") from exc
    return {"status": "ok", "version": __version__, "llm_backend": s.llm_backend, "adapter": s.adapter}


@app.get("/health/llm", tags=["ops"])
def health_llm() -> dict[str, Any]:
    from aap.llm import get_llm

    llm = get_llm()
    ok = getattr(llm, "health", lambda: True)()
    if not ok:
        raise HTTPException(503, "LLM backend not ready")
    return {"status": "ok", "backend": llm.backend}


# --------------------------------------------------------------- documents
@app.post("/api/documents", tags=["documents"], status_code=201)
def upload(session: DB, file: Annotated[UploadFile, File()],
           inline: Annotated[bool, Query(description="run the pipeline in this request (dev)")] = False,
           uploaded_by: str = "api") -> dict[str, Any]:
    data = file.file.read()
    try:
        res = intake(session, data, filename=file.filename, source="upload", uploaded_by=uploaded_by,
                     allow_duplicate=get_settings().allow_duplicate_uploads)
    except IntakeError as exc:
        raise HTTPException(415, str(exc)) from exc
    session.commit()
    doc_id = res.document.id
    if res.created:
        if inline:
            run_pipeline_inline(doc_id)
        else:
            from aap.worker.tasks import enqueue_stage

            enqueue_stage("parse", doc_id)
    return {"document_id": str(doc_id), "created": res.created,
            "duplicate_of": str(res.duplicate_of) if res.duplicate_of else None}


@app.get("/api/documents", tags=["documents"])
def list_documents(session: DB, status: str | None = None, limit: int = 100,
                   include_eval: bool = False) -> list[dict[str, Any]]:
    q = select(Document).order_by(Document.created_at.desc()).limit(min(limit, 500))
    if not include_eval:
        q = q.where(Document.source != "eval")
    if status:
        q = q.where(Document.status == status)
    return [{"document_id": str(d.id), "filename": d.filename, "file_type": d.file_type, "status": d.status,
             "source": d.source, "parent_id": str(d.parent_id) if d.parent_id else None, "flags": d.flags,
             "created_at": d.created_at.isoformat()} for d in session.scalars(q)]


@app.get("/api/documents/{document_id}", tags=["documents"])
def get_document(session: DB, document_id: uuid.UUID) -> dict[str, Any]:
    summary = document_summary(session, document_id)
    if not summary:
        raise HTTPException(404, "document not found")
    return summary


@app.get("/api/documents/{document_id}/file", tags=["documents"])
def get_file(session: DB, document_id: uuid.UUID) -> FileResponse:
    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    return FileResponse(FileStore().absolute(doc.storage_path), media_type=doc.mime_type or "application/octet-stream",
                        filename=doc.filename or f"{doc.id}.{doc.file_type}", content_disposition_type="inline")


@app.get("/api/documents/{document_id}/apload", tags=["documents"], response_class=PlainTextResponse)
def get_apload(session: DB, document_id: uuid.UUID) -> PlainTextResponse:
    """The invoice as APLOAD rows (the customer's pipe-delimited AP interface file). Before approval:
    the version a clerk would approve now (header `X-AAP-Approved: false`)."""
    from aap.submit.apload import AploadError, apload_text, document_invoice

    try:
        inv, approved = document_invoice(session, document_id)
        body = apload_text([inv])
    except AploadError as exc:
        raise HTTPException(409 if "missing" in str(exc) else 404, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - e.g. no invoice version yet
        raise HTTPException(409, f"no invoice to export: {exc}") from exc
    return PlainTextResponse(body, headers={"X-AAP-Approved": str(approved).lower()})


@app.post("/api/documents/{document_id}/retry", tags=["documents"])
def retry(session: DB, document_id: uuid.UUID) -> dict[str, Any]:
    """Administrator restart of a FAILED document from stage 3 (ARCHITECTURE 7)."""
    from aap.pipeline.status import Status, set_status

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    if doc.status != Status.FAILED.value:
        raise HTTPException(409, f"document is {doc.status}, not FAILED")
    set_status(session, doc, Status.RECEIVED, reason="administrator restart", actor="admin")
    session.commit()
    from aap.worker.tasks import enqueue_stage

    enqueue_stage("parse", doc.id)
    return {"document_id": str(doc.id), "status": doc.status}


@app.delete("/api/documents/{document_id}", tags=["documents"])
def delete_document(session: DB, document_id: uuid.UUID, by: str = "api") -> dict[str, Any]:
    """Remove a document and everything derived from it (an email goes with its attachments).
    The audit trail keeps a `document.deleted` event; an invoice already in Oracle stays there."""
    from aap.pipeline.delete import delete_document as run
    from aap.pipeline.delete import purge_files

    try:
        deleted = run(session, document_id, by=by)
    except LookupError as exc:
        raise HTTPException(404, "document not found") from exc
    session.commit()
    purge_files(session, deleted)
    return {"deleted": [str(i) for i in deleted.ids]}


# ------------------------------------------------------------------ agents
class Decision(BaseModel):
    action: str                      # approve | reject
    by: str = "clerk"
    note: str | None = None
    # approve only: {"po_number": "4500130", "lines": {"1": {"po_line_number": 1}, "2": {"dist_account": "01-510-6420-0000"}}}
    corrections: dict[str, Any] | None = None


@app.post("/api/documents/{document_id}/decision", tags=["documents"])
def decide_invoice(session: DB, document_id: uuid.UUID, decision: Decision, inline: bool = False) -> dict[str, Any]:
    """Approve -> frozen `approved` version -> stage 10 submit to Oracle -> stage 11 result.
    Reject -> REJECTED. Allowed from IN_REVIEW, ON_HOLD and ORACLE_ERROR."""
    from aap.submit.decision import DecisionError, approve, reject

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    try:
        if decision.action == "approve":
            version = approve(session, doc, by=decision.by, note=decision.note, corrections=decision.corrections)
        elif decision.action == "reject":
            reject(session, doc, by=decision.by, reason=decision.note or "rejected")
            session.commit()
            return {"document_id": str(doc.id), "status": doc.status}
        else:
            raise HTTPException(422, "action must be approve or reject")
    except DecisionError as exc:
        raise HTTPException(409, str(exc)) from exc
    session.commit()
    if inline:
        run_pipeline_inline_from(doc.id, "submit")
        session.refresh(doc)
        return {"document_id": str(doc.id), "status": doc.status, "version": version.version}
    from aap.worker.tasks import enqueue_stage

    enqueue_stage("submit", doc.id)
    return {"document_id": str(doc.id), "status": doc.status, "version": version.version}


class Correction(BaseModel):
    by: str = "clerk"
    note: str | None = None
    # Printed values, as on the invoice: {"invoice_number": "AII28916", "payment_terms": null}
    header: dict[str, str | None] | None = None
    # {"2": {"quantity": "4", "amount": "120.00"}}
    lines: dict[str, dict[str, str | None]] | None = None
    remove_lines: list[int] | None = None
    # [{"description": "Freight pallet", "amount": "40.00", "position": 3}]
    add_lines: list[dict[str, Any]] | None = None


@app.post("/api/documents/{document_id}/corrections", tags=["documents"])
def correct_document(session: DB, document_id: uuid.UUID, correction: Correction,
                     inline: bool = False) -> dict[str, Any]:
    """Correct what was read from the invoice. The corrected values go through stages 6-8 and the
    agents again; the correction is kept next to the model's reading (training label).
    Allowed from IN_REVIEW, ON_HOLD and ORACLE_ERROR."""
    from aap.submit.correct import correct_extraction
    from aap.submit.decision import DecisionError

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    try:
        run = correct_extraction(session, doc, by=correction.by, header=correction.header, lines=correction.lines,
                                 remove_lines=correction.remove_lines, add_lines=correction.add_lines,
                                 note=correction.note)
    except DecisionError as exc:
        raise HTTPException(409, str(exc)) from exc
    changes = run.raw_output[0]["changes"]
    session.commit()
    if inline:
        run_pipeline_inline_from(doc.id, "normalize")
        session.refresh(doc)
    else:
        from aap.worker.tasks import enqueue_stage

        enqueue_stage("normalize", doc.id)
    return {"document_id": str(doc.id), "status": doc.status, "extraction_run_id": str(run.id), "changes": changes}


@app.get("/api/documents/{document_id}/submissions", tags=["documents"])
def submissions(session: DB, document_id: uuid.UUID) -> list[dict[str, Any]]:
    from aap.db.models import InvoiceVersion, Submission

    rows = session.scalars(select(Submission).join(InvoiceVersion, InvoiceVersion.id == Submission.invoice_version_id)
                           .where(InvoiceVersion.document_id == document_id).order_by(Submission.created_at))
    return [{"idempotency_key": r.idempotency_key, "status": r.status, "group_id": r.group_id,
             "request_id": r.oracle_request_id, "interface_invoice_id": r.interface_invoice_id,
             "oracle_invoice_id": r.oracle_invoice_id, "oracle_holds": r.oracle_holds,
             "oracle_errors": r.oracle_errors, "polls": r.polls, "created_at": r.created_at.isoformat()}
            for r in rows]


@app.get("/api/review-tasks", tags=["agents"])
def review_tasks(session: DB, status: str = "open", limit: int = 200) -> list[dict[str, Any]]:
    rows = session.scalars(select(ReviewTask).where(ReviewTask.status == status)
                           .order_by(ReviewTask.created_at.desc()).limit(min(limit, 1000)))
    return [{"id": str(t.id), "document_id": str(t.document_id) if t.document_id else None,
             "reason_codes": t.reason_codes, "suggested_action": t.suggested_action, "summary": t.summary,
             "created_at": t.created_at.isoformat()} for t in rows]


@app.post("/api/documents/{document_id}/agents", tags=["agents"])
def rerun_agents(session: DB, document_id: uuid.UUID, inline: bool = False) -> dict[str, Any]:
    """(Re)run the agents on the latest invoice version."""
    from aap.pipeline.status import Status, set_status

    doc = session.get(Document, document_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    if doc.status not in (Status.VALIDATED.value, Status.IN_REVIEW.value, Status.ON_HOLD.value):
        raise HTTPException(409, f"document is {doc.status}")
    set_status(session, doc, Status.VALIDATED, reason="agents re-run requested", actor="api")
    session.commit()
    if inline:
        from aap.agents.runner import run_agents

        return {"document_id": str(document_id), "status": run_agents(document_id)}
    from aap.worker.tasks import enqueue_stage

    enqueue_stage("agents", document_id)
    return {"document_id": str(document_id), "status": "queued"}


@app.get("/api/documents/{document_id}/agents", tags=["agents"])
def agent_details(session: DB, document_id: uuid.UUID) -> dict[str, Any]:
    runs = session.scalars(select(AgentRun).where(AgentRun.document_id == document_id)
                           .order_by(AgentRun.created_at.desc()))
    decisions = session.scalars(select(AgentDecision).where(AgentDecision.document_id == document_id)
                                .order_by(AgentDecision.id))
    holds = session.scalars(select(Hold).where(Hold.document_id == document_id))
    tasks = session.scalars(select(ReviewTask).where(ReviewTask.document_id == document_id)
                            .order_by(ReviewTask.created_at))
    return {
        "runs": [{"id": str(r.id), "status": r.status, "outcome": r.outcome} for r in runs],
        "decisions": [{"node": d.node, "chosen": d.chosen, "reason": d.reason, "model": d.model,
                       "candidates": d.candidates} for d in decisions],
        "holds": [{"code": h.hold_lookup_code, "line": h.line_number, "status": h.status, "summary": h.summary}
                  for h in holds],
        "review_tasks": [{"status": t.status, "reason_codes": t.reason_codes, "suggested_action": t.suggested_action,
                          "summary": t.summary} for t in tasks],
    }


# ------------------------------------------------------------------- admin
@app.post("/api/admin/refsync", tags=["admin"])
def trigger_refsync(inline: bool = False) -> dict[str, Any]:
    if inline:
        from aap.adapters import get_read_adapter
        from aap.refsync.sync import run_sync

        with session_scope() as session:
            run = run_sync(session, get_read_adapter(), triggered_by="admin")
            return {"run_id": run.id, "status": run.status, "counts": run.counts}
    from aap.worker.app import defer

    job_id = defer("aap.refsync", "periodic", triggered_by="admin")
    return {"job_id": job_id, "status": "queued"}


@app.get("/api/value-map-proposals", tags=["admin"])
def value_map_proposals(session: DB, status: str = "pending", limit: int = 200) -> list[dict[str, Any]]:
    from aap.db.models import ValueMapProposal

    rows = session.scalars(select(ValueMapProposal).where(ValueMapProposal.status == status)
                           .order_by(ValueMapProposal.id.desc()).limit(min(limit, 1000)))
    return [{"id": p.id, "map_type": p.map_type, "source_value": p.source_value, "vendor_id": p.vendor_id,
             "oracle_value": p.oracle_value, "oracle_id": p.oracle_id, "status": p.status,
             "document_id": str(p.document_id) if p.document_id else None, "proposed_by": p.proposed_by,
             "decided_by": p.decided_by, "created_at": p.created_at.isoformat()} for p in rows]


class ProposalDecision(BaseModel):
    action: str                      # approve | reject
    by: str = "admin"


@app.post("/api/value-map-proposals/{proposal_id}/decision", tags=["admin"])
def decide_value_map_proposal(session: DB, proposal_id: int, decision: ProposalDecision) -> dict[str, Any]:
    """Approve -> an active value_map row: the printed value is mapped without the model from now on."""
    from aap.mapping.proposals import ProposalError, decide_proposal

    if decision.action not in ("approve", "reject"):
        raise HTTPException(422, "action must be approve or reject")
    try:
        p = decide_proposal(session, proposal_id, by=decision.by, approve=decision.action == "approve")
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProposalError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": p.id, "status": p.status}


# --------------------------------------------------------------------- UI
from fastapi.staticfiles import StaticFiles  # noqa: E402

from .ui import router as ui_router  # noqa: E402

app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(ui_router)
