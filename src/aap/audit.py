"""Audit log helper (F-10, ARCHITECTURE 10). INSERT-only."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from aap.db.models import AuditEvent


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=_default))


def _default(o: Any) -> Any:
    if isinstance(o, Decimal):
        return str(o)
    if isinstance(o, uuid.UUID):
        return str(o)
    if hasattr(o, "isoformat"):
        return o.isoformat()
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json")
    return str(o)


def audit(session: Session, event_type: str, *, document_id: uuid.UUID | str | None = None,
          actor: str = "system", **payload: Any) -> None:
    session.add(AuditEvent(
        document_id=uuid.UUID(str(document_id)) if document_id else None,
        actor=actor,
        event_type=event_type,
        payload=_jsonable(payload) if payload else None,
    ))
