"""decide (PLAN-MODEL-JSON, adapter B): the model writes the Oracle fields of a non-PO invoice from its
printed values and a context pack, and the code checks them.

DECIDE_MODE:

- `off` (default, system S0): nothing happens; GL coding is the code's (gl_code).
- `shadow`: the model is asked and its answer is stored (agent_decision, agent_run.outcome["decide"])
  with every difference from the code's values; the invoice and its review reasons do not change.
- `apply` (systems S1 and S2): the model's account (or distribution set) is used on every item line.
  The checks stay: an account that differs from the code's, a supplier without history (fallback
  pack), a null answer or an invalid combination sends the line to review (GL_REVIEW). Header values
  (invoice type, terms, payment method, pay group, liability account) stay the code's; one the model
  wrote differently adds MODEL_DIFFERS, so a person looks before anything is created.

The model is DECIDE_MODEL: the base model for S1, the adapter (DECIDE_ADAPTER_MODEL) for S2.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from aap.adapters import get_read_adapter
from aap.common.coa import layout_from_settings
from aap.config import get_settings
from aap.decide.context import build_context_pack
from aap.decide.history import from_db
from aap.decide.request import PROMPT_VERSION, decide_request, printed_from_invoice
from aap.decide.schema import HEADER_FIELDS
from aap.llm import get_llm
from aap.llm.client import LlmError

from ..context import add, decide, work
from ..state import AgentState, Reason


def set_account(pack: dict[str, Any], name: str) -> tuple[str | None, bool]:
    """The account a distribution set puts a line on, and whether that is one account only (a set
    that splits the line goes to review: the JSON carries one distribution per line)."""
    ds = next((s for s in pack.get("distribution_sets") or [] if s.get("name") == name), None)
    if ds is None:
        return None, False
    lines = [x for x in ds.get("lines") or [] if x.get("account")]
    if len(lines) == 1:
        return lines[0]["account"], True
    keyed = ds.get("keyed_accounts") or []
    if keyed:
        return keyed[0]["account"], len(keyed) == 1
    return None, False


def _code_header(inv: Any, pack: dict[str, Any]) -> dict[str, Any]:
    return {"invoice_type_lookup_code": inv.invoice_type.value or "STANDARD", "terms_name": inv.terms_name.value,
            "payment_method_code": inv.payment_method.value, "pay_group_lookup_code": inv.pay_group.value,
            "liability_account": (pack.get("site") or {}).get("liability_account")}


def compare(answer: dict[str, Any], inv: Any, pack: dict[str, Any], gl_lines: list[dict[str, Any]]
            ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(header differences, line results) between the model's answer and the code's values."""
    code = _code_header(inv, pack)
    header = [{"field": k, "model": answer.get(k), "code": code[k]} for k in HEADER_FIELDS
              if code[k] is not None and answer.get(k) != code[k]]
    gl = {g["line"]: g for g in gl_lines}
    item_lines = {ln.line_number for ln in inv.item_lines()}
    lines = []
    for a in answer.get("lines") or []:
        n = a.get("line_number")
        if n not in item_lines:
            continue                       # freight and miscellaneous lines take Oracle's setup
        account, single = (a.get("account"), True)
        if account is None and a.get("distribution_set"):
            account, single = set_account(pack, a["distribution_set"])
        code_account = gl.get(n, {}).get("account")
        lines.append({"line": n, "model": account, "code": code_account, "basis": a.get("basis"),
                      "distribution_set": a.get("distribution_set"), "single_account": single,
                      "agrees": account is not None and account == code_account})
    return header, lines


def decide_fields(state: AgentState) -> dict[str, Any]:
    s = get_settings()
    if s.decide_mode == "off" or state.get("route") != "non_po":
        return {}
    coa = layout_from_settings()
    with work(state) as w:
        inv = w.inv
        if not inv.vendor_id.value:
            decide(w, state, "decide", reason="no supplier: the model is not asked")
            return {}
        as_of: date = inv.gl_date.value or inv.invoice_date.value or date.today()
        store = from_db(w.session, inv.vendor_id.value, as_of=as_of)
        pack = build_context_pack(store, vendor_id=inv.vendor_id.value, vendor_site_id=inv.vendor_site_id.value,
                                  as_of=as_of, printed_store=inv.location_ref.source_value or inv.location_ref.value)
        printed = printed_from_invoice(inv)
        if not printed["lines"]:
            decide(w, state, "decide", reason="no lines: the model is not asked")
            return {}
        req = decide_request(printed, pack, coa=coa, model=s.decide_model, max_tokens=s.decide_max_tokens,
                             context={"document_id": str(w.doc.id)})
        try:
            resp = get_llm().ask(req)
        except LlmError as exc:
            decide(w, state, "decide", reason=f"model failed: {exc}", raw=exc.raw, model=s.decide_model)
            return {"notes": add(state, "notes", f"Adapter B ({s.decide_model}) failed: {exc}; the code's values stand")}
        answer = resp.data
        header, lines = compare(answer, inv, pack, state.get("gl_lines") or [])
        summary = {"mode": s.decide_mode, "model": resp.model, "prompt_version": PROMPT_VERSION,
                   "context_pack_version": pack["context_pack_version"], "as_of": as_of.isoformat(),
                   "history_scope": pack["history_scope"], "fallback": pack["fallback"], "answer": answer,
                   "header_diffs": header, "lines": lines,
                   "agrees": not header and all(x["agrees"] for x in lines)}
        decide(w, state, "decide", chosen=answer,
               inputs={"as_of": as_of.isoformat(), "history_scope": pack["history_scope"], "fallback": pack["fallback"],
                       "accounts": len(pack["accounts"]), "distribution_sets": len(pack["distribution_sets"])},
               reason="agrees with the code" if summary["agrees"] else
               "; ".join([f"{d['field']}: model {d['model']} / code {d['code']}" for d in header]
                         + [f"line {x['line']}: model {x['model']} / code {x['code']}" for x in lines
                            if not x["agrees"]]),
               prompt=req.content[0]["text"], raw=resp.raw, model=resp.model)
    notes = [f"Adapter B: {d['field']} {d['model']!r}, code {d['code']!r}" for d in header]
    notes += [f"Adapter B: line {x['line']} {x['model'] or 'no account'} ({x['basis']}), code {x['code']}"
              for x in lines if not x["agrees"]]
    out: dict[str, Any] = {"decide": summary}
    if s.decide_mode == "shadow":
        if notes:
            out["notes"] = add(state, "notes", *notes)
        return out
    out["gl_lines"] = _apply(state.get("gl_lines") or [], lines, pack, inv)
    reasons = []
    if any(not g.get("auto") for g in out["gl_lines"]):
        reasons.append(Reason.GL_REVIEW)
    if header:
        reasons.append(Reason.MODEL_DIFFERS)
    if reasons:
        out["reasons"] = add(state, "reasons", *reasons)
    if notes:
        out["notes"] = add(state, "notes", *notes)
    return out


def _apply(gl_lines: list[dict[str, Any]], lines: list[dict[str, Any]], pack: dict[str, Any], inv: Any
           ) -> list[dict[str, Any]]:
    """The model's accounts on the item lines; anything not certain to review."""
    adapter = get_read_adapter()
    by_line = {x["line"]: x for x in lines}
    out = []
    for g in gl_lines:
        g = dict(g)
        x = by_line.get(g["line"])
        if x is None:
            out.append(g)
            continue
        if x["model"] is None:
            g.update(auto=False, reason=f"{g.get('reason')}; the model found no candidate account")
        elif x["agrees"] and x["single_account"] and not pack["fallback"]:
            g.update(reason=f"{g.get('reason')}; the model agrees ({x['basis']})", model_agrees=True)
        else:
            why = ("supplier without history" if pack["fallback"] else
                   "the set splits the line" if not x["single_account"] else f"code had {x['code']}")
            g.update(account=x["model"], method="decide", auto=False, confidence=0.5, code_account=x["code"],
                     reason=f"model chose {x['model']} ({x['basis']}"
                            + (f", set {x['distribution_set']}" if x["distribution_set"] else "") + f"); {why}")
            check = adapter.ccid_valid(x["model"], inv.gl_date.value or inv.invoice_date.value)
            g["ccid"] = check.ccid
            if not check.valid:
                g["reason"] += f"; {x['model']} is not valid"
        out.append(g)
    return out
