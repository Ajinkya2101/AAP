"""Document status model (ARCHITECTURE 7 + plan 7 additions)."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy.orm import Session

from aap.audit import audit
from aap.db.models import Document, DocumentStatusHistory


class Status(StrEnum):
    RECEIVED = "RECEIVED"
    UNPACKED = "UNPACKED"          # email container: children created (terminal for the container)
    DUPLICATE = "DUPLICATE"        # same file already received (terminal)
    PARSED = "PARSED"
    ROUTED = "ROUTED"
    EXTRACTED = "EXTRACTED"
    NORMALIZED = "NORMALIZED"
    MAPPED = "MAPPED"
    VALIDATED = "VALIDATED"
    AGENTS_RUNNING = "AGENTS_RUNNING"
    IN_REVIEW = "IN_REVIEW"
    ON_HOLD = "ON_HOLD"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    CREATED = "CREATED"
    ORACLE_ERROR = "ORACLE_ERROR"
    NOT_INVOICE = "NOT_INVOICE"    # e.g. an email body that is not an invoice
    FAILED = "FAILED"


# Linear order of the automatic pipeline (stages 1-8).
PIPELINE_ORDER = [Status.RECEIVED, Status.PARSED, Status.ROUTED, Status.EXTRACTED, Status.NORMALIZED,
                  Status.MAPPED, Status.VALIDATED]


def reached(doc: Document, status: Status) -> bool:
    """True if the document is at or past `status` in the pipeline order."""
    try:
        return PIPELINE_ORDER.index(Status(doc.status)) >= PIPELINE_ORDER.index(status)
    except ValueError:
        return Status(doc.status) not in (Status.FAILED,)


def set_status(session: Session, doc: Document, new: Status, *, reason: str | None = None,
               actor: str = "pipeline") -> None:
    old = doc.status
    if old == new:
        return
    doc.status = new.value
    if new == Status.FAILED:
        doc.error = reason
    session.add(DocumentStatusHistory(document_id=doc.id, from_status=old, to_status=new.value, reason=reason))
    audit(session, "document.status", document_id=doc.id, actor=actor, from_status=old, to_status=new.value,
          reason=reason)
