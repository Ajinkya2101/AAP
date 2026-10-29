"""recheck_holds (plan 8): ON_HOLD -> AGENTS_RUNNING when Oracle's receipts change.

For each ON_HOLD document with a proposed QTY REC hold, compare the received quantity
of the held PO lines (live PO read) with what the three-way check saw. Any change ->
the agents run again from triage; finalize releases holds that no longer apply.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from aap.adapters import get_read_adapter
from aap.audit import audit
from aap.db.models import AgentRun, Document, Hold
from aap.db.session import session_scope
from aap.logging import get_logger
from aap.pipeline.status import Status, set_status

log = get_logger(__name__)

RECHECK_CODES = ("QTY REC",)


def _dec(v: object) -> Decimal | None:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def changed_documents(limit: int = 200) -> list[tuple[uuid.UUID, str]]:
    """(document_id, reason) for held documents whose receipts moved since the last run."""
    adapter = get_read_adapter()
    out: list[tuple[uuid.UUID, str]] = []
    with session_scope() as s:
        docs = s.scalars(select(Document).where(
            Document.status == Status.ON_HOLD.value,
            Document.id.in_(select(Hold.document_id).where(Hold.status == "proposed",
                                                           Hold.hold_lookup_code.in_(RECHECK_CODES))))
            .limit(limit)).all()
        for doc in docs:
            run = s.scalars(select(AgentRun).where(AgentRun.document_id == doc.id, AgentRun.status == "done")
                            .order_by(AgentRun.created_at.desc()).limit(1)).first()
            outcome = (run.outcome if run else None) or {}
            po_number = outcome.get("po_number")
            held = [v for v in outcome.get("variances") or [] if v.get("hold") in RECHECK_CODES]
            if not po_number or not held:
                continue
            received: dict[tuple[str, int], Decimal] = {}
            for ref in {(v.get("po") or po_number, v.get("release")) for v in held}:
                po = adapter.get_po(ref[0])
                if po is not None:
                    po = po.for_release(ref[1]) if po.blanket and ref[1] is not None else po
                    received.update({(po.reference, pl.line_num): pl.received for pl in po.lines})
            moved = []
            for v in held:
                key = (v.get("ref") or po_number, v["po_line"])
                if key in received and _dec(v.get("received")) != received[key]:
                    moved.append(f"PO {key[0]} line {v['po_line']}: received {v.get('received')} -> {received[key]}")
            if moved:
                out.append((doc.id, "; ".join(moved)))
    return out


def recheck_holds(*, inline: bool = False, limit: int = 200) -> list[str]:
    restarted = []
    for doc_id, why in changed_documents(limit):
        with session_scope() as s:
            doc = s.get(Document, doc_id)
            if doc is None or doc.status != Status.ON_HOLD.value:
                continue
            set_status(s, doc, Status.AGENTS_RUNNING, reason=f"receipt changed: {why}", actor="recheck_holds")
            audit(s, "agents.recheck", document_id=doc.id, actor="recheck_holds", reason=why)
        log.info("agents.recheck", document_id=str(doc_id), reason=why)
        if inline:
            from aap.agents.runner import run_agents

            run_agents(doc_id)
        else:
            from aap.worker.tasks import enqueue_stage

            enqueue_stage("agents", doc_id)
        restarted.append(str(doc_id))
    return restarted
