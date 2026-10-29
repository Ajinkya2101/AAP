"""triage (code): validation results -> route.

Order matters. A changed bank account must be confirmed by phone on a number already
on file. Problems the supplier must fix on the invoice go to review with that request
as the suggested action (this build sends no vendor email).
"""

from __future__ import annotations

from typing import Any

from aap.schemas.fields import Flag

from ..context import decide, latest_results, work
from ..state import AgentState, Reason

# Invoice maths the supplier must correct (line maths, subtotal, tax, grand total).
FAILS_VENDOR_MUST_FIX = {"C2", "C3", "C4", "C5"}


def triage(state: AgentState) -> dict[str, Any]:
    with work(state) as w:
        results = latest_results(w.session, w.version.id)
        fails = {r.rule_id: r for r in results if r.result == "fail"}
        warns = {r.rule_id: r for r in results if r.result == "warning"}
        flags = set(w.doc.flags or [])
        inv = w.inv
        supplier_po_required = _po_required(w.session, inv.vendor_id.value)

        reasons = list(state.get("reasons") or [])
        notes = list(state.get("notes") or [])
        route, action = None, None

        def note(r) -> None:  # noqa: ANN001
            notes.append(f"{r.rule_id}: {r.message}")

        if "C11" in fails or Flag.unknown_sender in flags:
            if "C11" in fails:
                reasons.append(Reason.BANK_CHANGE)
                note(fails["C11"])
            if Flag.unknown_sender in flags:
                reasons.append(Reason.UNKNOWN_SENDER)
                notes.append("The sender's email domain is not on any supplier site.")
            route, action = "review", "verify_bank_details_by_phone"
        elif "C10" in fails:
            reasons.append(Reason.DUPLICATE)
            note(fails["C10"])
            route, action = "review", "reject_as_duplicate"
        elif "C8" in fails:
            reasons.append(Reason.SUPPLIER_INACTIVE)
            note(fails["C8"])
            route, action = "review", "correct_supplier_site"
        elif "V-PO" in fails:
            reasons.append(Reason.PO_NOT_OPEN)
            for r in results:              # one result per PO the invoice names
                if r.rule_id == "V-PO" and r.result == "fail":
                    note(r)
            route, action = "review", "contact_buyer_to_reopen_po_or_reject"
        elif fails.keys() & FAILS_VENDOR_MUST_FIX and inv.vendor_id.value:
            reasons.append(Reason.INVOICE_MATH)
            problems = [fails[k].message for k in sorted(fails.keys() & FAILS_VENDOR_MUST_FIX)]
            notes.append("The amounts on the invoice do not add up: " + "; ".join(problems) + ".")
            route, action = "review", "ask_vendor_for_corrected_invoice"
        elif fails:
            reasons.append(Reason.DATA_MISSING if "C1" in fails or "V-05" in fails else Reason.VALIDATION_FAILED)
            for r in fails.values():
                note(r)
            route, action = "review", "correct_invoice_data"
        else:
            if "C9" in warns:
                reasons.append(Reason.DATE_AMBIGUOUS)
                note(warns["C9"])
            if any(r.rule_id == "V-05" and "chosen by the model" in (r.message or "") for r in results):
                reasons.append(Reason.CHECK_MAPPING)
            route = "po" if inv.po_refs() or supplier_po_required else "non_po"

        decide(w, state, "triage", chosen=route, inputs={"fails": sorted(fails), "warnings": sorted(warns),
                                                         "flags": sorted(flags)},
               reason="; ".join(notes[-3:]) or None)
    out: dict[str, Any] = {"route": route, "reasons": list(dict.fromkeys(reasons)), "notes": notes}
    if action:
        out["suggested_action"] = action
    return out


def _po_required(session, vendor_id) -> bool:  # noqa: ANN001
    from aap.db.models import RefSupplier

    s = session.get(RefSupplier, vendor_id) if vendor_id else None
    return bool(s and s.po_required)
