"""Clerk decisions on a reviewed invoice (approve / reject).

Approve freezes an `approved` invoice version (R-05: append-only) that carries the
agents' decisions the posting needs: the PO line each invoice line is paired with,
and the GL account of every non-PO line. Stage 10 submits exactly that version.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from aap.audit import audit
from aap.db.models import AgentRun, Document, GlHistoryLine, InvoiceVersion, RefCcid, ReviewTask
from aap.mapping.proposals import propose_value_maps
from aap.pipeline.stages import _new_version, latest_version
from aap.pipeline.status import Status, set_status
from aap.schemas.canonical import CanonicalInvoice
from aap.schemas.fields import Field, Method

APPROVABLE = {Status.IN_REVIEW.value, Status.ON_HOLD.value, Status.ORACLE_ERROR.value}


class DecisionError(ValueError):
    pass


def _check(doc: Document, allowed: set[str]) -> None:
    if doc.status not in allowed:
        raise DecisionError(f"document is {doc.status}; expected one of {sorted(allowed)}")


def _close_tasks(session: Session, doc: Document, by: str) -> None:
    for t in session.scalars(select(ReviewTask).where(ReviewTask.document_id == doc.id, ReviewTask.status == "open")):
        t.status, t.resolved_by, t.resolved_at = "done", by, datetime.now(UTC)


def approved_invoice(session: Session, doc: Document) -> tuple[CanonicalInvoice, InvoiceVersion, dict[int, Any]]:
    """The latest reviewed version plus the agents' pairing and GL decisions."""
    base = latest_version(session, doc.id, ["mapped", "user", "vendor_reply"])
    if base is None:
        raise DecisionError("no invoice version to approve")
    inv = CanonicalInvoice.model_validate(base.canonical_invoice)
    run = session.scalars(select(AgentRun).where(AgentRun.document_id == doc.id, AgentRun.status == "done")
                          .order_by(AgentRun.created_at.desc()).limit(1)).first()
    outcome = (run.outcome if run else None) or {}
    pairs = {p["line"]: p for p in outcome.get("pairs") or []}
    gl = {g["line"]: g for g in outcome.get("gl_lines") or []}
    po_number = outcome.get("po_number")
    if po_number and not inv.po_number.value and outcome.get("po_source") != "invoice" \
            and len(outcome.get("po_refs") or [None]) == 1:
        inv.po_number = Field(value=po_number, method=Method.derived, note="PO found by the agents")
    for ln in inv.item_lines():
        p = pairs.get(ln.line_number)
        if p and (p.get("po") or po_number):
            ln.po_number = Field(value=p.get("po") or po_number, method=Method.derived)
            ln.po_release_num = Field(value=p.get("release"), method=Method.derived)
            ln.po_line_number = Field(value=p["po_line"], method=Method.derived,
                                      note=f"paired by the agents ({p['method']})")
        elif gl.get(ln.line_number, {}).get("account") and not ln.dist_account.value:
            g = gl[ln.line_number]
            ln.dist_account = Field(value=g["account"], method=Method.derived, note=f"GL agent ({g['method']})")
    refs = outcome.get("po_refs") or []
    if len(refs) == 1 and refs[0]["po"] == inv.po_number.value and refs[0].get("release") is not None \
            and inv.po_release_num.value is None:
        inv.po_release_num = Field(value=refs[0]["release"], method=Method.derived,
                                   note="the agreement's only open release")
    return inv, base, gl


def apply_corrections(inv: CanonicalInvoice, corrections: dict[str, Any] | None, by: str) -> None:
    """Clerk corrections from the review screen: {"po_number": ..., "lines": {"<n>": {"po_number": ..,
    "po_line_number": .., "dist_account": ..}}}. They win over the agents' proposals (method=user).
    A line's PO is "4500123" or, for a blanket release, "4500210-2"."""
    if not corrections:
        return
    if corrections.get("po_number"):
        inv.po_number = Field(value=str(corrections["po_number"]), method=Method.user, note=f"set by {by}")
    lines = {ln.line_number: ln for ln in inv.lines}
    for key, change in (corrections.get("lines") or {}).items():
        ln = lines.get(int(key))
        if ln is None:
            raise DecisionError(f"invoice has no line {key}")
        if change.get("po_number"):
            po, _, release = str(change["po_number"]).strip().partition("-")
            if not po or (release and not release.isdigit()):
                raise DecisionError(f"line {key}: PO {change['po_number']!r} is not a PO number (4500123 or 4500210-2)")
            ln.po_number = Field(value=po, method=Method.user, note=f"set by {by}")
            ln.po_release_num = Field(value=int(release) if release else None, method=Method.user)
        if change.get("po_line_number") is not None:
            ln.po_line_number = Field(value=int(change["po_line_number"]), method=Method.user, note=f"set by {by}")
            if not ln.po_number.value:
                ln.po_number = Field(value=inv.po_number.value, method=Method.derived)
                ln.po_release_num = inv.po_release_num.model_copy()
            if not ln.po_number.value:
                raise DecisionError(f"line {key} has a PO line but no PO number")
            ln.dist_account = Field(value=None, method=Method.derived)
        if change.get("dist_account"):
            ln.dist_account = Field(value=str(change["dist_account"]), method=Method.user, note=f"set by {by}")
            ln.po_line_number = Field(value=None, method=Method.derived)


def approve(session: Session, doc: Document, *, by: str, note: str | None = None,
            corrections: dict[str, Any] | None = None) -> InvoiceVersion:
    _check(doc, APPROVABLE)
    inv, base, gl = approved_invoice(session, doc)
    apply_corrections(inv, corrections, by)
    missing = [ln.line_number for ln in inv.item_lines()
               if ln.po_line_number.value is None and not ln.dist_account.value]
    if missing:
        raise DecisionError(f"line(s) {missing} have neither a PO line nor a GL account")
    version = _new_version(session, doc.id, "approved", inv, parent=base, created_by=by)
    learned = learn_gl(session, inv, gl)
    proposed = propose_value_maps(session, doc.id, inv, by)
    from_status = doc.status
    _close_tasks(session, doc, by)
    set_status(session, doc, Status.APPROVED, reason=note or f"approved by {by}", actor=by)
    audit(session, "invoice.approved", document_id=doc.id, actor=by, version=version.version, note=note,
          from_status=from_status, corrections=corrections, gl_history_added=learned,
          value_maps_proposed=proposed)
    return version


def learn_gl(session: Session, inv: CanonicalInvoice, gl: dict[int, Any]) -> int:
    """Plan 8 feedback: a GL account a person decided (the agents were unsure, or the clerk
    corrected it) becomes GL history at once, so the next identical line is coded from
    exact history without the model. Automatic codings are not repeated here; they reach
    the history through the reference sync once the invoice is in Oracle."""
    from aap.embed import get_embedder

    added = []
    for ln in inv.item_lines():
        acct = ln.dist_account.value
        human = ln.dist_account.method == Method.user or not gl.get(ln.line_number, {}).get("auto", True)
        if not acct or ln.po_line_number.value is not None or not inv.vendor_id.value or not human:
            continue
        ccid = session.scalar(select(RefCcid.ccid).where(RefCcid.concatenated == acct))
        if ccid is None:
            continue
        added.append(GlHistoryLine(vendor_id=inv.vendor_id.value, org_id=inv.org_id.value,
                                   description=ln.description.value or "", ccid=ccid, concatenated=acct,
                                   natural_account=acct.split("-")[2], amount=ln.amount.value,
                                   invoice_date=inv.invoice_date.value, source="review"))
    emb = get_embedder() if added else None
    if emb is not None:
        for row, vec in zip(added, emb.embed([r.description for r in added]), strict=True):
            row.embedding = vec.tolist()
    session.add_all(added)
    return len(added)


def reject(session: Session, doc: Document, *, by: str, reason: str) -> None:
    _check(doc, APPROVABLE)
    _close_tasks(session, doc, by)
    set_status(session, doc, Status.REJECTED, reason=reason, actor=by)
    audit(session, "invoice.rejected", document_id=doc.id, actor=by, reason=reason)
