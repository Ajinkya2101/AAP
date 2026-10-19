"""Value-map proposals (ARCHITECTURE 6.5): what the model chose and a clerk approved becomes
a proposed alias. Once the functional team approves it, the same printed value is mapped
by the value map (step 1), without the model.

Only supplier and payment terms: the two value maps stage 7 reads that the model can reach.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from aap.audit import audit
from aap.common.text import text_key
from aap.db.models import ValueMap, ValueMapProposal
from aap.schemas.canonical import CanonicalInvoice
from aap.schemas.fields import Method


class ProposalError(ValueError):
    pass


def _known(session: Session, map_type: str, key: str, vendor_id: int | None) -> bool:
    scope = (ValueMap.vendor_id == vendor_id) if vendor_id is not None else ValueMap.vendor_id.is_(None)
    if session.scalar(select(ValueMap.id).where(ValueMap.map_type == map_type, ValueMap.source_key == key,
                                                ValueMap.status == "active", scope).limit(1)):
        return True
    scope = (ValueMapProposal.vendor_id == vendor_id) if vendor_id is not None \
        else ValueMapProposal.vendor_id.is_(None)
    return bool(session.scalar(select(ValueMapProposal.id).where(
        ValueMapProposal.map_type == map_type, ValueMapProposal.source_key == key,
        ValueMapProposal.status == "pending", scope).limit(1)))


def propose_value_maps(session: Session, document_id: uuid.UUID, inv: CanonicalInvoice, by: str) -> int:
    """Called when a clerk approves an invoice. Returns the number of proposals added."""
    wanted: list[tuple[str, str | None, int | None, str | None, int | None]] = []
    if inv.vendor_id.method == Method.fuzzy_llm and inv.vendor_id.value:
        wanted.append(("supplier", inv.vendor_id.source_value, None, inv.vendor_name.value, inv.vendor_id.value))
    if inv.terms_id.method == Method.fuzzy_llm and inv.terms_id.value:
        wanted.append(("terms", inv.terms_id.source_value, inv.vendor_id.value, inv.terms_name.value,
                       inv.terms_id.value))
    added = 0
    for map_type, source, vendor_id, oracle_value, oracle_id in wanted:
        key = text_key(source)
        if not key or not oracle_value or _known(session, map_type, key, vendor_id):
            continue
        session.add(ValueMapProposal(map_type=map_type, source_value=source, source_key=key, vendor_id=vendor_id,
                                     oracle_value=oracle_value, oracle_id=oracle_id, document_id=document_id,
                                     status="pending", proposed_by=by))
        added += 1
    return added


def decide_proposal(session: Session, proposal_id: int, *, by: str, approve: bool) -> ValueMapProposal:
    p = session.get(ValueMapProposal, proposal_id)
    if p is None:
        raise LookupError(f"value-map proposal {proposal_id} not found")
    if p.status != "pending":
        raise ProposalError(f"proposal is already {p.status}")
    p.status, p.decided_by, p.decided_at = ("approved" if approve else "rejected"), by, datetime.now(UTC)
    if approve:
        session.add(ValueMap(map_type=p.map_type, source_value=p.source_value, source_key=p.source_key,
                             vendor_id=p.vendor_id, oracle_value=p.oracle_value, oracle_id=p.oracle_id,
                             status="active", created_by=p.proposed_by, approved_by=by))
    audit(session, "value_map.proposal_decided", document_id=p.document_id, actor=by, proposal_id=p.id,
          map_type=p.map_type, source_value=p.source_value, oracle_value=p.oracle_value, decision=p.status)
    return p
