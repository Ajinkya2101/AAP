"""Helpers every node uses: load the working version, record decisions (plan 8: every
node writes an agent_decision row and an audit_event row)."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aap.audit import audit
from aap.db.models import AgentDecision, Document, InvoiceVersion, ValidationResult
from aap.db.session import session_scope
from aap.schemas.canonical import CanonicalInvoice

from .state import AgentState


@dataclass
class Work:
    session: Session
    doc: Document
    version: InvoiceVersion
    inv: CanonicalInvoice


@contextmanager
def work(state: AgentState):
    with session_scope() as s:
        doc = s.get(Document, uuid.UUID(state["document_id"]))
        version = s.get(InvoiceVersion, uuid.UUID(state["version_id"]))
        if doc is None or version is None:
            raise LookupError(f"document/version missing for {state['document_id']}")
        yield Work(s, doc, version, CanonicalInvoice.model_validate(version.canonical_invoice))


def latest_results(session: Session, version_id: uuid.UUID) -> list[ValidationResult]:
    run = session.scalar(select(func.max(ValidationResult.run_no))
                         .where(ValidationResult.invoice_version_id == version_id))
    if run is None:
        return []
    return list(session.scalars(select(ValidationResult).where(
        ValidationResult.invoice_version_id == version_id, ValidationResult.run_no == run)))


def decide(w: Work, state: AgentState, node: str, *, chosen: Any = None, inputs: dict | None = None,
           candidates: list | None = None, reason: str | None = None, confidence: float | None = None,
           prompt: str | None = None, raw: str | None = None, model: str | None = None) -> None:
    w.session.add(AgentDecision(
        agent_run_id=uuid.UUID(state["run_id"]) if state.get("run_id") else None, document_id=w.doc.id,
        node=node, inputs=inputs, candidates=candidates, chosen={"value": chosen} if chosen is not None else None,
        reason=reason, confidence=confidence, prompt=prompt, raw_output=raw, model=model))
    audit(w.session, f"agent.{node}", document_id=w.doc.id, actor="agents", chosen=chosen, reason=reason)


def add(state: AgentState, key: str, *values: Any) -> list[Any]:
    """Returns a new list (state values are replaced, not mutated)."""
    out = list(state.get(key) or [])
    for v in values:
        if v not in out:
            out.append(v)
    return out
