"""gl_code (WORKED-EXAMPLE step 7).

PO lines: the account the buyer charged on the PO distribution (EBS copies it; no model).
Non-PO lines: exact history -> similar history (all top 5 agree, similarity >=
GL_SIM_AUTO) -> LLM chooses among the accounts found, or "review". Every proposed
combination is checked live with ccid_valid.
"""

from __future__ import annotations

import uuid
from typing import Any

from aap.adapters import get_read_adapter
from aap.config import get_settings
from aap.llm import get_llm
from aap.mapping.choose import choose

from ..context import add, decide, work
from ..state import AgentState, Reason
from ..tools.gl import account_description, exact_history, majority, similar_history, template_ccid
from .matching import _load_po


def gl_code(state: AgentState) -> dict[str, Any]:
    s = get_settings()
    adapter = get_read_adapter()
    gl_lines: list[dict[str, Any]] = []
    with work(state) as w:
        inv = w.inv
        pairs = {p["line"]: p for p in state.get("pairs") or []}
        po = _load_po(state, inv.org_id.value) if pairs else None
        po_lines = {pl.line_num: pl for pl in po.lines} if po else {}
        for ln in inv.item_lines():
            desc = ln.description.value or ""
            entry: dict[str, Any] = {"line": ln.line_number, "description": desc}
            if ln.line_number in pairs and po is not None:
                pl = po_lines[pairs[ln.line_number]["po_line"]]
                ship = pl.shipments[0]
                entry.update(account=ship.ccid_concatenated, method="po_distribution", auto=True, confidence=1.0,
                             reason=f"from PO {po.po_number} line {pl.line_num} distribution")
            else:
                entry.update(_non_po(w, state, s.gl_sim_auto, inv.vendor_id.value, desc, ln.line_number))
            if entry.get("account"):
                check = adapter.ccid_valid(entry["account"], inv.gl_date.value or inv.invoice_date.value)
                entry["ccid"] = check.ccid
                if not check.valid:
                    entry.update(auto=False, reason=(entry.get("reason") or "") + f"; {entry['account']} is not valid "
                                 f"(exists={check.exists}, enabled={check.enabled}, active={check.active_on_date})")
            gl_lines.append(entry)
        decide(w, state, "gl_code", chosen=[(g["line"], g.get("account"), g["method"]) for g in gl_lines],
               reason="; ".join(f"line {g['line']}: {g.get('reason')}" for g in gl_lines))
    review = [g for g in gl_lines if not g.get("auto")]
    out: dict[str, Any] = {"gl_lines": gl_lines}
    if review:
        out["reasons"] = add(state, "reasons", Reason.GL_REVIEW)
        out["notes"] = add(state, "notes", *[f"GL line {g['line']} ({g['description']}): {g.get('account') or 'no account'}"
                                             f" - {g.get('reason')}" for g in review])
    return out


def _non_po(w, state: AgentState, sim_auto: float, vendor_id: int, desc: str,  # noqa: ANN001
            line: int | None = None) -> dict[str, Any]:
    exact = exact_history(w.session, vendor_id, desc)
    if exact:
        acct, n, total = majority([h.natural_account for h in exact])
        concat = majority([h.concatenated for h in exact if h.natural_account == acct])[0]
        if n == total:
            return dict(account=concat, method="exact_history", auto=True, confidence=1.0,
                        reason=f"{total} past line(s) with the same text, all coded {acct}")
        others = sorted({h.natural_account for h in exact if h.natural_account != acct})
        s = get_settings()
        if total >= s.gl_exact_min and n / total >= s.gl_exact_auto:
            return dict(account=concat, method="exact_history", auto=True, confidence=round(n / total, 3),
                        reason=f"{n} of {total} past lines with the same text coded {acct}; "
                               f"{', '.join(others)} ({total - n}) treated as past miscoding")
        return dict(account=concat, method="exact_history", auto=False, confidence=round(n / total, 3),
                    reason=f"{n} of {total} past lines with the same text coded {acct} (also {', '.join(others)})")
    hits = similar_history(w.session, vendor_id, desc)
    if not hits:
        return dict(account=None, method="none", auto=False, confidence=0.0, reason="no history for this supplier")
    accounts = {h.natural_account for h in hits}
    if len(accounts) == 1 and min(h.similarity for h in hits) >= sim_auto:
        acct = accounts.pop()
        return dict(account=template_ccid(w.session, vendor_id, acct), method="similar_history", auto=True,
                    confidence=min(h.similarity for h in hits),
                    reason=f"{len(hits)} of {len(hits)} similar past lines coded {acct}")
    cands = []
    for acct in sorted(accounts):
        past = [h for h in hits if h.natural_account == acct]
        cands.append({"id": acct, "label": f"{acct} = {account_description(w.session, acct)}; past lines: "
                      + "; ".join(f"\"{h.description}\" ({h.similarity:.2f})" for h in past)})
    pick = choose(w.session, get_llm(), document_id=w.doc.id, node="agent.gl_code",
                  question="Choose the GL natural account for this invoice line. Use the past decisions and the "
                           "account descriptions.", value=desc, candidates=cands, allow_review=True,
                  agent_run_id=uuid.UUID(state["run_id"]), line=line)
    if pick.id is None:
        return dict(account=None, method="llm", auto=False, confidence=0.0, reason=f"model: review ({pick.reason})")
    # A model decision always goes to review (WORKED-EXAMPLE 2); the clerk's confirmation
    # is added to the history so the next identical line is coded without a model.
    return dict(account=template_ccid(w.session, vendor_id, pick.id), method="llm", auto=False, confidence=0.5,
                reason=f"model chose {pick.id}: {pick.reason}")
