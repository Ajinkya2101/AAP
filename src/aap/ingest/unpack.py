"""Stage 2 Unpack (ARCHITECTURE 6.2).

- One PDF / image / sheet attachment: that attachment is the invoice.
- No attachment: the email body is the invoice (if it looks like one).
- More than one possible invoice: each becomes a document, flagged `multiple_invoices`.
- Sender domain not in any supplier site email: children flagged `unknown_sender` (S15).
- Auto-replies are not invoices (S14).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import any_, func, literal, select
from sqlalchemy.orm import Session

from aap.audit import audit
from aap.common.text import email_domain
from aap.config import get_settings
from aap.db.models import Document, EmailAttachment, EmailMessage, RefSupplierSite
from aap.filestore import FileStore, sha256_bytes
from aap.pipeline.status import Status, set_status
from aap.schemas.fields import Flag

from .emailparse import EMAIL_HEADER_KEYS, ParsedEmail, parse_eml, parse_msg
from .filetypes import detect
from .intake import IntakeError, intake

INVOICE_TYPES = {"pdf", "png", "jpg", "tiff", "xlsx", "xls", "csv"}
INVOICE_WORDS = re.compile(r"(?i)\b(invoice|rechnung|factura|facture|bill\s+no|tax\s+invoice|amount\s+due|total\s+due)\b")
AMOUNT = re.compile(r"\d[\d,.]*\d")


@dataclass
class UnpackResult:
    children: list[Document] = field(default_factory=list)
    outcome: str = "invoice"          # invoice | not_invoice | auto_reply


def known_sender(session: Session, from_addr: str | None) -> bool | None:
    """True/False, or None when there is no reference data to decide."""
    if session.scalar(select(func.count()).select_from(RefSupplierSite)) == 0:
        return None
    domain = email_domain(from_addr)
    if not domain:
        return False
    hit = session.scalar(select(func.count()).select_from(RefSupplierSite)
                         .where(literal(domain) == any_(RefSupplierSite.email_domains)))
    return bool(hit)


def _email_row(session: Session, doc: Document, parsed: ParsedEmail) -> EmailMessage:
    if doc.email_message_id:
        row = session.get(EmailMessage, doc.email_message_id)
        if row is not None:
            return row
    row = EmailMessage(
        id=uuid.uuid4(), direction="inbound", message_id=parsed.message_id, in_reply_to=parsed.in_reply_to,
        references=parsed.references, from_addr=parsed.from_addr, to_addrs=parsed.to_addrs,
        cc_addrs=parsed.cc_addrs, subject=parsed.subject, sent_at=parsed.date, raw_sha256=doc.sha256,
        raw_path=doc.storage_path, document_id=doc.id,
        headers={k: v for k, v in parsed.headers.items() if k in EMAIL_HEADER_KEYS},
    )
    session.add(row)
    session.flush()
    doc.email_message_id = row.id
    return row


def unpack(session: Session, doc: Document, store: FileStore | None = None) -> UnpackResult:
    store = store or FileStore()
    raw = store.read(doc.storage_path)
    parsed = parse_msg(raw) if doc.file_type == "msg" else parse_eml(raw)
    email_row = _email_row(session, doc, parsed)
    result = UnpackResult()

    if parsed.is_auto_reply:
        email_row.classification = "auto_reply"
        doc.flags = [*doc.flags, "auto_reply"]
        result.outcome = "auto_reply"
        set_status(session, doc, Status.NOT_INVOICE, reason="auto-reply email")
        return result

    sender_known = known_sender(session, parsed.from_addr)
    child_flags: list[str] = []
    if sender_known is False:
        child_flags.append(Flag.unknown_sender)

    max_bytes = get_settings().max_upload_mb * 1024 * 1024
    # Children stay in the parent's eval sandbox (aap.eval); otherwise they are "unpack" documents.
    child_source = "eval" if doc.source == "eval" else "unpack"
    child_by = doc.uploaded_by if doc.source == "eval" else None
    candidates: list[tuple[bytes, str | None, EmailAttachment]] = []
    for att in parsed.attachments:
        kind = detect(att.data, att.filename) if att.data else "unknown"
        rec = EmailAttachment(email_message_id=email_row.id, filename=att.filename, content_type=att.content_type,
                              size_bytes=len(att.data), sha256=sha256_bytes(att.data))
        session.add(rec)
        if len(att.data) > max_bytes:
            rec.skipped_reason = "too_large"
        elif kind == "zip":
            rec.skipped_reason = "zip_flagged_for_review"
            doc.flags = [*doc.flags, "zip_attachment"]
        elif kind in INVOICE_TYPES:
            candidates.append((att.data, att.filename, rec))
        else:
            rec.skipped_reason = f"not_invoice_type:{kind}"

    if len(candidates) > 1:
        child_flags.append(Flag.multiple_invoices)

    if candidates:
        for data, filename, rec in candidates:
            try:
                res = intake(session, data, filename=filename, source=child_source, parent=doc, role="attachment",
                             email_message_id=email_row.id, flags=child_flags, store=store, uploaded_by=child_by)
            except IntakeError as exc:
                rec.skipped_reason = f"rejected:{exc}"[:100]
                audit(session, "unpack.attachment_rejected", document_id=doc.id, filename=filename, error=str(exc))
                continue
            rec.document_id = res.document.id
            result.children.append(res.document)
        email_row.classification = "invoice"
    else:
        body = parsed.text_body.strip()
        if body and INVOICE_WORDS.search(body) and AMOUNT.search(body):
            res = intake(session, body.encode("utf-8"), filename="email-body.txt", source=child_source, parent=doc,
                         role="body", email_message_id=email_row.id, flags=child_flags, store=store,
                         uploaded_by=child_by)
            result.children.append(res.document)
            email_row.classification = "invoice"
        else:
            email_row.classification = "other"
            result.outcome = "not_invoice"

    if result.children:
        email_row.document_id = result.children[0].id if len(result.children) == 1 else doc.id
        set_status(session, doc, Status.UNPACKED, reason=f"{len(result.children)} document(s)")
    else:
        set_status(session, doc, Status.NOT_INVOICE, reason="no invoice attachment or invoice-like body")
    audit(session, "unpack.completed", document_id=doc.id, children=[str(c.id) for c in result.children],
          sender=parsed.from_addr, sender_known=sender_known, outcome=result.outcome)
    return result
