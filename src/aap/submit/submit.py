"""Stages 10 Submit and 11 Result (plan 7) against the ErpWritePort.

Submit: the `approved` version -> Payables Open Interface + APXIIMPT. The idempotency
key is `<document_id>:<version>` (R-07); it is unique on `submission` and stored in
AP_INVOICES_INTERFACE.ATTRIBUTE15, so a retried job never posts twice.
Result: poll the import -> CREATED (+ app holds applied in Oracle) or ORACLE_ERROR
with the reject codes. Polling is a delayed job, not a sleep in a worker.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from aap.adapters import get_write_adapter
from aap.adapters.base import SubmitRef
from aap.audit import audit
from aap.db.models import Document, Hold, ReviewTask, Submission
from aap.db.session import session_scope
from aap.logging import get_logger
from aap.pipeline.stages import NextSteps, _expect, latest_version
from aap.pipeline.status import Status, set_status
from aap.schemas.canonical import CanonicalInvoice

log = get_logger(__name__)

METHOD = "ebs_open_interface"
MAX_POLLS = 120          # x POLL_SECONDS: give up after ~10 minutes and send to review
POLL_SECONDS = 5


def idempotency_key(document_id: uuid.UUID, version: int) -> str:
    return f"{document_id}:{version}"


def stage_submit(session: Session, doc: Document) -> NextSteps:
    _expect(doc, Status.APPROVED, Status.SUBMITTED)
    version = latest_version(session, doc.id, ["approved"])
    if version is None:
        raise RuntimeError("no approved version")
    key = idempotency_key(doc.id, version.version)
    sub = session.scalars(select(Submission).where(Submission.idempotency_key == key)).first()
    if sub is None:
        sub = Submission(invoice_version_id=version.id, idempotency_key=key, method=METHOD, status="pending")
        session.add(sub)
        session.flush()
    if sub.status in ("created", "error"):
        log.info("submit.already_done", document_id=str(doc.id), key=key, status=sub.status)
        return []
    adapter = get_write_adapter()
    payload = adapter.to_payload(CanonicalInvoice.model_validate(version.canonical_invoice))
    ref = adapter.submit(payload, key)
    sub.group_id, sub.oracle_request_id, sub.interface_invoice_id = ref.group_id, ref.request_id, \
        ref.interface_invoice_id
    sub.status = "submitted"
    set_status(session, doc, Status.SUBMITTED, reason=f"APXIIMPT request {ref.request_id}", actor="submit")
    audit(session, "submit.sent", document_id=doc.id, actor="submit", key=key, group_id=ref.group_id,
          request_id=ref.request_id, interface_invoice_id=ref.interface_invoice_id,
          payload=payload.model_dump(mode="json"))
    return [("result", doc.id)]


def check_result(document_id: uuid.UUID | str) -> str:
    """One poll. Returns the submission status: submitted (still pending) | created | error."""
    doc_id = uuid.UUID(str(document_id))
    with session_scope() as session:
        doc = session.scalar(select(Document).where(Document.id == doc_id).with_for_update())
        if doc is None:
            raise LookupError(doc_id)
        sub = session.scalars(select(Submission).where(Submission.status == "submitted",
                                                       Submission.idempotency_key.like(f"{doc_id}:%"))
                              .order_by(Submission.created_at.desc()).limit(1)).first()
        if sub is None:
            return "none"
        ref = SubmitRef(idempotency_key=sub.idempotency_key, group_id=sub.group_id or "",
                        interface_invoice_id=sub.interface_invoice_id or 0, request_id=sub.oracle_request_id)
        adapter = get_write_adapter()
        status = adapter.get_status(ref)
        sub.polls = (sub.polls or 0) + 1
        if status.state == "pending":
            if sub.polls >= MAX_POLLS:
                session.add(ReviewTask(document_id=doc.id, reason_codes=["ORACLE_TIMEOUT"],
                                       summary=f"Oracle import request {sub.oracle_request_id} has not finished "
                                               f"after {sub.polls} checks ({status.detail or 'no detail'}).",
                                       suggested_action="check_oracle_import", status="open"))
                sub.status = "error"
                sub.oracle_errors = ["TIMEOUT"]
                set_status(session, doc, Status.ORACLE_ERROR, reason="import did not finish", actor="result")
                return "error"
            return "submitted"
        if status.state == "created":
            sub.status, sub.oracle_invoice_id, sub.oracle_holds = "created", str(status.oracle_invoice_id), \
                status.holds
            placed = set(status.holds)
            for h in session.scalars(select(Hold).where(Hold.document_id == doc.id, Hold.status == "proposed")):
                if h.hold_lookup_code not in placed and status.oracle_invoice_id:
                    adapter.apply_hold(status.oracle_invoice_id, h.hold_lookup_code, h.summary)
                    placed.add(h.hold_lookup_code)
                h.status = "active"
            set_status(session, doc, Status.CREATED, actor="result",
                       reason=f"Oracle invoice {status.oracle_invoice_id}"
                              + (f", holds: {', '.join(sorted(placed))}" if placed else ""))
            audit(session, "submit.created", document_id=doc.id, actor="result",
                  oracle_invoice_id=status.oracle_invoice_id, holds=sorted(placed), detail=status.detail)
            return "created"
        sub.status, sub.oracle_errors = "error", status.errors
        session.add(ReviewTask(document_id=doc.id, reason_codes=["ORACLE_ERROR", *status.errors],
                               summary="Oracle rejected the invoice: " + ", ".join(status.errors),
                               suggested_action="fix_and_resubmit", status="open"))
        set_status(session, doc, Status.ORACLE_ERROR, reason=", ".join(status.errors), actor="result")
        audit(session, "submit.rejected", document_id=doc.id, actor="result", errors=status.errors,
              detail=status.detail)
        return "error"


def wait_for_result(document_id: uuid.UUID | str, *, timeout: float = 60, run_import=None) -> str:  # noqa: ANN001
    """Inline mode (dev / tests): poll until the import finished. `run_import` can drive the
    mock concurrent manager in-process when the ebs-cm container is not running."""
    deadline = time.monotonic() + timeout
    while True:
        if run_import is not None:
            run_import()
        state = check_result(document_id)
        if state != "submitted" or time.monotonic() > deadline:
            return state
        time.sleep(0.5)

