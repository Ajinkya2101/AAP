"""finalize: one review task with the reasons and the agents' findings (auto-post is
off in this build, plan 8), then IN_REVIEW or ON_HOLD."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from aap.db.models import AgentRun, Hold, ReviewTask
from aap.pipeline.status import Status, set_status

from ..context import decide, work
from ..state import AgentState, Reason

ACTIONS = {
    Reason.QTY_VARIANCE: "hold_until_received_or_credit_note",
    Reason.PRICE_VARIANCE: "hold_and_resolve_price",
    Reason.GL_REVIEW: "confirm_gl_accounts",
    Reason.PO_SUGGESTED: "confirm_suggested_po",
    Reason.DATE_AMBIGUOUS: "confirm_invoice_date",
    Reason.RELEASE_MISSING: "ask_vendor_for_release_number",
}


def finalize(state: AgentState) -> dict[str, Any]:
    reasons = list(state.get("reasons") or [])
    on_hold = state.get("outcome") == "ON_HOLD"
    action = state.get("suggested_action") or next((ACTIONS[r] for r in reasons if r in ACTIONS), None) \
        or "approve"
    notes = state.get("notes") or []
    matched = [f"line {p['line']} -> PO {p.get('ref') or state.get('po_number')} line {p['po_line']} ({p['method']})"
               for p in state.get("pairs") or []]
    gl = [f"line {g['line']}: {g.get('account')} ({g['method']})" for g in state.get("gl_lines") or []]
    parts = list(notes)
    if matched:
        parts.append("PO matching: " + "; ".join(matched))
    if gl:
        parts.append("GL: " + "; ".join(gl))
    summary = "\n".join(parts) or "All checks passed; ready for approval."
    with work(state) as w:
        for t in w.session.scalars(select(ReviewTask).where(ReviewTask.document_id == w.doc.id,
                                                            ReviewTask.status == "open")):
            t.status, t.resolved_by, t.resolved_at = "done", "agents", datetime.now(UTC)
        # A re-run (recheck_holds) may clear an earlier variance: release its hold.
        current = {(v["hold"], v["line"]) for v in state.get("variances") or []} if on_hold else set()
        for h in w.session.scalars(select(Hold).where(Hold.document_id == w.doc.id, Hold.status == "proposed")):
            if (h.hold_lookup_code, h.line_number) not in current:
                h.status, h.released_at = "released", datetime.now(UTC)
                parts.append(f"Hold {h.hold_lookup_code} on line {h.line_number} released: no longer applies.")
        summary = "\n".join(parts) or summary
        w.session.add(ReviewTask(document_id=w.doc.id, reason_codes=reasons, summary=summary,
                                 suggested_action=action, status="open"))
        outcome = Status.ON_HOLD if on_hold else Status.IN_REVIEW
        set_status(w.session, w.doc, outcome, reason=", ".join(reasons) or "clean", actor="agents")
        run = w.session.get(AgentRun, uuid.UUID(state["run_id"]))
        if run is not None:
            run.status, run.finished_at = "done", datetime.now(UTC)
            run.outcome = {"status": outcome.value, "reasons": reasons, "suggested_action": action,
                           "po_number": state.get("po_number"), "po_source": state.get("po_source"),
                           "po_refs": state.get("po_refs"),
                           "pairs": state.get("pairs"),
                           "gl_lines": state.get("gl_lines"), "variances": state.get("variances")}
        decide(w, state, "finalize", chosen=outcome.value, reason=", ".join(reasons) or "clean")
    return {"outcome": outcome.value, "reasons": reasons, "suggested_action": action}
