"""PO matching: find_po -> pair_lines -> three_way_compare -> hold (WORKED-EXAMPLE 1 and 3).

Arithmetic and the 3-way comparison are code. The LLM only picks between PO
lines when the score matrix cannot decide (enum of candidate line numbers).
Receipts and billed quantities are read live from Oracle at decision time.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from aap.adapters import get_read_adapter
from aap.adapters.base import PoDetail
from aap.config import get_settings
from aap.db.models import Hold, RefSupplierSite, RefTolerance
from aap.llm import get_llm
from aap.mapping.choose import choose

from ..context import add, decide, work
from ..state import AgentState, Reason
from ..tools.pairing import InvLine, assign, score_matrix

ZERO = Decimal(0)


def _dec(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def _fmt(q: Decimal) -> str:
    return f"{q.normalize():f}" if q == q.to_integral_value() else f"{q:f}"


# ------------------------------------------------------------------ find_po
def find_po(state: AgentState) -> dict[str, Any]:
    adapter = get_read_adapter()
    with work(state) as w:
        inv = w.inv
        if inv.po_number.value:
            decide(w, state, "find_po", chosen=inv.po_number.value, reason="PO number printed on the invoice")
            return {"po_number": inv.po_number.value, "po_source": state.get("po_source") or "invoice"}
        items = inv.item_lines()
        base = inv.subtotal.value or sum((_dec(ln.amount.value) or ZERO for ln in items), ZERO)
        prices = sorted({_dec(ln.unit_price.value) for ln in items if ln.unit_price.value is not None})
        hits = adapter.search_open_pos(inv.vendor_id.value, inv.currency_code.value, Decimal(str(base)), prices)
        cands = [{"po_number": h.po_number, "open_amount": str(h.open_amount),
                  "matching_prices": [str(p) for p in h.matching_prices]} for h in hits]
        if len(hits) == 1:
            h = hits[0]
            decide(w, state, "find_po", chosen=h.po_number, candidates=cands,
                   reason=f"only open PO of the supplier with open amount >= {base} and "
                          f"{len(h.matching_prices)} matching unit price(s)")
            return {"po_number": h.po_number, "po_source": "search",
                    "reasons": add(state, "reasons", Reason.PO_MISSING, Reason.PO_SUGGESTED),
                    "notes": add(state, "notes", f"No PO number on the invoice. PO {h.po_number} is likely: "
                                                 f"{len(h.matching_prices)} unit price(s) match.")}
        why = "no open PO fits" if not hits else f"{len(hits)} open POs fit ({', '.join(c['po_number'] for c in cands)})"
        decide(w, state, "find_po", chosen=None, candidates=cands, reason=why)
        return {"po_number": None, "reasons": add(state, "reasons", Reason.PO_MISSING),
                "notes": add(state, "notes", f"No PO number on the invoice and {why}. Ask the vendor for the "
                                             f"PO number, or enter the PO lines on approval."),
                "suggested_action": "ask_vendor_for_po_number"}


# --------------------------------------------------------------- pair_lines
def _inv_lines(inv) -> list[InvLine]:  # noqa: ANN001
    out = []
    for ln in inv.item_lines():
        qty, price, amount = _dec(ln.quantity.value), _dec(ln.unit_price.value), _dec(ln.amount.value)
        out.append(InvLine(ln.line_number, ln.description.value or "", qty, price, amount))
    return out


def _load_po(state: AgentState, org_id: int | None) -> PoDetail | None:
    return get_read_adapter().get_po(state["po_number"], org_id) if state.get("po_number") else None


def pair_lines(state: AgentState) -> dict[str, Any]:
    s = get_settings()
    with work(state) as w:
        po = _load_po(state, w.inv.org_id.value)
        if po is None:
            decide(w, state, "pair_lines", reason=f"PO {state.get('po_number')} not found")
            return {"pairs": [], "unmatched": [], "notes": add(state, "notes", f"PO {state.get('po_number')} not found.")}
        po_lines = [pl for pl in po.lines if not pl.cancelled and pl.shipments]
        inv_lines = _inv_lines(w.inv)
        matrix = score_matrix(inv_lines, po_lines)
        assignment = assign(matrix)
        pairs: list[dict[str, Any]] = []
        unmatched: list[int] = []
        taken: set[int] = set()
        reasons = list(state.get("reasons") or [])
        ambiguous: list[int] = []
        for i, il in enumerate(inv_lines):
            row = sorted(matrix[i], key=lambda c: c.score, reverse=True)
            j = assignment.get(i)
            chosen = matrix[i][j] if j is not None else None
            second = next((c.score for c in row if chosen is None or c.po_line_num != chosen.po_line_num), 0.0)
            if chosen and chosen.score >= s.match_auto_accept and chosen.score - second >= s.match_margin:
                pairs.append(dict(line=il.line_number, po_line=chosen.po_line_num, score=chosen.score,
                                  method="score", reason=f"score {chosen.score:.2f} vs next {second:.2f}"))
                taken.add(chosen.po_line_num)
            else:
                ambiguous.append(i)
        for i in ambiguous:
            il = inv_lines[i]
            row = [c for c in sorted(matrix[i], key=lambda c: c.score, reverse=True)
                   if c.po_line_num not in taken and c.score >= 0.3][:5]
            if not row:
                unmatched.append(il.line_number)
                continue
            by_num = {pl.line_num: pl for pl in po_lines}
            cands = [{"id": str(c.po_line_num), "score": c.score,
                      "label": f"PO line {c.po_line_num}: \"{by_num[c.po_line_num].item_description}\", "
                               f"qty open {_fmt(by_num[c.po_line_num].ordered - by_num[c.po_line_num].billed)}, "
                               f"{by_num[c.po_line_num].unit_price}"} for c in row]
            pick = choose(w.session, get_llm(), document_id=w.doc.id, node="agent.pair_lines",
                          question="Match the invoice line to the purchase order line that describes the same product.",
                          value=f"{il.description}, qty {il.quantity}, {il.unit_price}", candidates=cands,
                          agent_run_id=uuid.UUID(state["run_id"]), line=il.line_number)
            if pick.id is None:
                unmatched.append(il.line_number)
                continue
            num = int(pick.id)
            score = next(c.score for c in row if c.po_line_num == num)
            pairs.append(dict(line=il.line_number, po_line=num, score=score, method="llm", reason=pick.reason))
            taken.add(num)
            reasons.append(Reason.LINE_PAIRED_BY_MODEL)
        if unmatched:
            reasons.append(Reason.LINE_NOT_ON_PO)
        decide(w, state, "pair_lines", chosen=pairs,
               candidates=[[c.__dict__ for c in row] for row in matrix],
               reason=f"{len(pairs)} paired, {len(unmatched)} unmatched")
    notes = list(state.get("notes") or [])
    if unmatched:
        notes.append(f"Invoice line(s) {', '.join(map(str, unmatched))} match no line of PO {state['po_number']}.")
    return {"pairs": pairs, "unmatched": unmatched, "reasons": list(dict.fromkeys(reasons)), "notes": notes}


# ---------------------------------------------------------------- three_way
def _tolerance(session, site_id: int | None) -> RefTolerance | None:  # noqa: ANN001
    site = session.get(RefSupplierSite, site_id) if site_id else None
    return session.get(RefTolerance, site.tolerance_id) if site and site.tolerance_id else None


def three_way(state: AgentState) -> dict[str, Any]:
    """Code only. Receipts and billed quantities come live from Oracle (the PO read)."""
    variances: list[dict[str, Any]] = []
    with work(state) as w:
        po = _load_po(state, w.inv.org_id.value)
        assert po is not None
        tol = _tolerance(w.session, w.inv.vendor_site_id.value)
        pct = lambda v: Decimal(v or 0) / 100  # noqa: E731
        by_num = {pl.line_num: pl for pl in po.lines}
        inv_by_num = {il.line_number: il for il in _inv_lines(w.inv)}
        checked = []
        for p in state.get("pairs") or []:
            il, pl = inv_by_num[p["line"]], by_num[p["po_line"]]
            qty = il.quantity if il.quantity is not None else (
                (il.amount / il.unit_price) if il.amount is not None and il.unit_price else None)
            price = il.unit_price if il.unit_price is not None else (
                (il.amount / qty) if il.amount is not None and qty else None)
            receipt_required = any(s.receipt_required for s in pl.shipments)
            open_received = pl.received - pl.billed
            open_ordered = pl.ordered - pl.billed
            row = dict(line=il.line_number, po_line=pl.line_num, description=il.description,
                       qty=str(qty), price=str(price), po_price=str(pl.unit_price), received=str(pl.received),
                       billed=str(pl.billed), ordered=str(pl.ordered))
            checked.append(row)
            if qty is not None and receipt_required and qty > open_received * (1 + pct(tol and tol.qty_received_tolerance)):
                short = qty - open_received
                variances.append(dict(row, hold="QTY REC", kind=Reason.QTY_VARIANCE, summary=(
                    f"Line {il.line_number} ({il.description}): billed {_fmt(qty)}, received {_fmt(pl.received)}"
                    + (f" (already billed {_fmt(pl.billed)})" if pl.billed else "")
                    + f" on PO {po.po_number} line {pl.line_num}. "
                    + ("The price matches. " if price is not None and abs(price - pl.unit_price) < Decimal("0.005") else "")
                    + f"Suggested action: hold the invoice until the remaining {_fmt(short)} unit(s) are received, "
                      f"or ask the vendor for a credit note for {_fmt(short)} unit(s).")))
            elif qty is not None and qty > open_ordered * (1 + pct(tol and tol.quantity_tolerance)):
                variances.append(dict(row, hold="QTY ORD", kind=Reason.QTY_VARIANCE, summary=(
                    f"Line {il.line_number} ({il.description}): billed {_fmt(qty)} but only {_fmt(open_ordered)} "
                    f"are open on PO {po.po_number} line {pl.line_num}. Suggested action: ask the vendor for a "
                    f"credit note for {_fmt(qty - open_ordered)} unit(s).")))
            if price is not None and price > pl.unit_price * (1 + pct(tol and tol.price_tolerance)):
                diff = (price - pl.unit_price) / pl.unit_price * 100
                variances.append(dict(row, hold="PRICE", kind=Reason.PRICE_VARIANCE, summary=(
                    f"Line {il.line_number} ({il.description}): unit price {price} vs PO {pl.unit_price} "
                    f"(+{diff:.1f}%, tolerance {Decimal(tol.price_tolerance if tol else 0):f}%) on PO {po.po_number} "
                    f"line {pl.line_num}. Suggested action: hold, and ask the buyer to amend the PO price or the "
                    f"vendor for a credit note of {(price - pl.unit_price) * (qty or 1):.2f}.")))
        decide(w, state, "three_way", chosen=[v["hold"] for v in variances], inputs={"checked": checked},
               reason="; ".join(v["summary"] for v in variances) or "all paired lines within tolerance")
    return {"variances": variances,
            "reasons": add(state, "reasons", *[v["kind"] for v in variances]),
            "notes": add(state, "notes", *[v["summary"] for v in variances])}


# --------------------------------------------------------------------- hold
def hold(state: AgentState) -> dict[str, Any]:
    with work(state) as w:
        existing = {(h.hold_lookup_code, h.line_number) for h in w.session.scalars(
            select(Hold).where(Hold.document_id == w.doc.id, Hold.status == "proposed"))}
        for v in state.get("variances") or []:
            if (v["hold"], v["line"]) in existing:
                continue
            w.session.add(Hold(document_id=w.doc.id, invoice_version_id=w.version.id, hold_lookup_code=v["hold"],
                               line_number=v["line"], summary=v["summary"], status="proposed"))
        decide(w, state, "hold", chosen=[v["hold"] for v in state.get("variances") or []],
               reason="hold proposed; applied in Oracle after approval (Phase 9)")
    return {"suggested_action": "hold_until_resolved", "outcome": "ON_HOLD"}
