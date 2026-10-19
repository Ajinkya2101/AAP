"""Delete a document and everything derived from it (dashboard "Delete").

Removes the document's rows in the aap database, its page images, its agent checkpoints
and, when no other document shares it, the stored original. An email container goes with
its attachments, and with its last attachment. Two things stay on purpose:

- `audit_event` rows (the application role cannot delete them); a `document.deleted`
  event records who removed what.
- Anything already in Oracle: an invoice that was posted stays posted.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from aap.audit import audit
from aap.db.models import (
    AgentDecision,
    AgentRun,
    Document,
    DocumentStatusHistory,
    EmailAttachment,
    EmailMessage,
    EvalResult,
    ExtractionRun,
    FieldEvidence,
    Hold,
    InvoiceVersion,
    Page,
    ReviewTask,
    Submission,
    ValidationResult,
    ValueMapProposal,
)
from aap.filestore import FileStore
from aap.logging import get_logger

log = get_logger(__name__)


@dataclass
class Deleted:
    ids: list[uuid.UUID] = field(default_factory=list)
    storage_paths: set[str] = field(default_factory=set)


def _family(session: Session, doc: Document) -> list[uuid.UUID]:
    """The document, its descendants, and its email container when nothing else is left in it."""
    ids, frontier = [doc.id], [doc.id]
    while frontier:
        frontier = list(session.scalars(select(Document.id).where(Document.parent_id.in_(frontier))))
        ids.extend(frontier)
    if doc.parent_id and not session.scalar(select(func.count()).select_from(Document).where(
            Document.parent_id == doc.parent_id, Document.id.not_in(ids))):
        ids.append(doc.parent_id)
    return ids


def delete_document(session: Session, document_id: uuid.UUID, *, by: str) -> Deleted:
    """Database part. Commit, then call purge_files() with the result."""
    doc = session.get(Document, document_id)
    if doc is None:
        raise LookupError(f"document {document_id} not found")
    ids = _family(session, doc)
    # Waits for a stage that is working on one of them (stages hold the row lock).
    docs = session.scalars(select(Document).where(Document.id.in_(ids)).with_for_update()).all()
    out = Deleted(ids=[d.id for d in docs], storage_paths={d.storage_path for d in docs})
    emails = {d.email_message_id for d in docs if d.email_message_id}
    for d in docs:
        audit(session, "document.deleted", document_id=d.id, actor=by, filename=d.filename, sha256=d.sha256,
              status=d.status, file_type=d.file_type, with_document=str(document_id) if d.id != document_id else None)
        session.expunge(d)

    versions = select(InvoiceVersion.id).where(InvoiceVersion.document_id.in_(ids))
    for model in (Submission, ValidationResult, FieldEvidence):
        session.execute(delete(model).where(model.invoice_version_id.in_(versions)))
    for model in (Hold, AgentDecision, AgentRun, ReviewTask, DocumentStatusHistory, Page, InvoiceVersion,
                  ExtractionRun):
        session.execute(delete(model).where(model.document_id.in_(ids)))
    for model in (EvalResult, ValueMapProposal, EmailAttachment, EmailMessage):
        session.execute(update(model).where(model.document_id.in_(ids)).values(document_id=None))
    session.execute(delete(Document).where(Document.id.in_(ids)))
    if emails:
        used = set(session.scalars(select(Document.email_message_id).where(Document.email_message_id.in_(emails))))
        gone = emails - used
        if gone:
            session.execute(delete(ReviewTask).where(ReviewTask.email_message_id.in_(gone)))
            session.execute(delete(EmailAttachment).where(EmailAttachment.email_message_id.in_(gone)))
            session.execute(delete(EmailMessage).where(EmailMessage.id.in_(gone)))
    return out


def purge_files(session: Session, deleted: Deleted, store: FileStore | None = None) -> None:
    """After the commit: files and agent checkpoints. Best effort; a leftover file is harmless."""
    from aap.agents.runner import forget_thread

    store = store or FileStore()
    shared = set(session.scalars(select(Document.storage_path).where(
        Document.storage_path.in_(deleted.storage_paths)))) if deleted.storage_paths else set()
    for rel in deleted.storage_paths - shared:
        store.absolute(rel).unlink(missing_ok=True)
    for doc_id in deleted.ids:
        shutil.rmtree(store.root / "pages" / str(doc_id), ignore_errors=True)
        try:
            forget_thread(doc_id)
        except Exception:  # noqa: BLE001
            log.exception("delete.checkpoints_failed", document_id=str(doc_id))
