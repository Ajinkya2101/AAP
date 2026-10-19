"""Stage 1 Intake: hash, real file type, store the original (ARCHITECTURE 6.2)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from aap.audit import audit
from aap.config import get_settings
from aap.db.models import Document
from aap.filestore import FileStore, sha256_bytes
from aap.pipeline.status import Status

from .filetypes import ACCEPTED, MIME, detect


class IntakeError(ValueError):
    pass


@dataclass
class IntakeResult:
    document: Document
    created: bool
    duplicate_of: uuid.UUID | None = None


def intake(
    session: Session,
    data: bytes,
    *,
    filename: str | None,
    source: str,
    uploaded_by: str | None = None,
    parent: Document | None = None,
    role: str | None = None,
    email_message_id: uuid.UUID | None = None,
    flags: list[str] | None = None,
    store: FileStore | None = None,
    allow_duplicate: bool = False,
) -> IntakeResult:
    settings = get_settings()
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise IntakeError(f"file is larger than {settings.max_upload_mb} MB")
    if not data:
        raise IntakeError("file is empty")
    file_type = detect(data, filename)
    if file_type not in ACCEPTED:
        raise IntakeError(f"file type not accepted: {file_type}")

    sha = sha256_bytes(data)
    if parent is None and not allow_duplicate:
        existing = session.scalars(
            select(Document).where(Document.sha256 == sha, Document.parent_id.is_(None))
            .order_by(Document.created_at).limit(1)).first()
        if existing is not None:
            audit(session, "intake.duplicate_file", document_id=existing.id, actor=uploaded_by or source,
                  filename=filename, sha256=sha)
            return IntakeResult(document=existing, created=False, duplicate_of=existing.id)

    store = store or FileStore()
    _, rel = store.put_original(data)
    doc = Document(
        id=uuid.uuid4(), sha256=sha, file_type=file_type, mime_type=MIME.get(file_type), filename=filename,
        size_bytes=len(data), storage_path=rel, source=source, status=Status.RECEIVED.value,
        parent_id=parent.id if parent else None, email_message_id=email_message_id, role=role,
        flags=list(flags or []), uploaded_by=uploaded_by,
    )
    session.add(doc)
    session.flush()
    audit(session, "intake.received", document_id=doc.id, actor=uploaded_by or source, filename=filename,
          sha256=sha, file_type=file_type, size=len(data), parent_id=str(parent.id) if parent else None)
    return IntakeResult(document=doc, created=True)
