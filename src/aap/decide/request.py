"""Adapter B's request (PLAN-MODEL-JSON §2-§5): the printed values and the context pack in, the Oracle
fields with their basis out. One function builds it for production (`node.py`) and for the training
dataset (`aap.finetune.chat decide`), so the model is trained on exactly what it is sent.

Printed values come from adapter A's reading in production (`printed_from_invoice`). For the datasets
built before the invoice files arrive they come from the keyed record (`printed_from_keyed`, marked
`printed_from: keyed`): number, dates, amounts, currency, line texts and amounts, nothing AP decided.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from typing import Any

from aap.common.coa import CoaLayout
from aap.llm.client import LlmRequest

from .history import TAX_TYPES
from .schema import HEADER_FIELDS, account_candidates, decide_schema

PROMPT_VERSION = "decide/1"
SYSTEM = (
    "You decide the Oracle Payables fields of one supplier invoice. You get the values printed on the "
    "invoice and a context pack from Oracle: the supplier site's defaults, the accounts and distribution "
    "sets this supplier was coded to before this invoice (with their share of the amount and typical line "
    "texts), its recent invoices, the instance's lists and the store table. Answer with the JSON of the "
    "schema. Choose every Oracle value among the candidates the schema allows; when none fits, answer null "
    "and that value goes to a person. For each line give either an account or a distribution set. Keep the "
    "site's defaults (terms, payment method, pay group, liability account) unless the invoice clearly says "
    "otherwise. A credit memo has negative amounts or says so in its title. For every value say where it "
    "came from in basis: printed (read from the invoice), site_default, history (an account this supplier "
    "used before), distribution_set, or chosen (your choice among the candidates without one of those).")


def _s(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return str(v.quantize(Decimal("0.01")))
    return str(v)


def decide_lines(lines: list[dict[str, Any]]) -> list[int]:
    return [int(ln["line_number"]) for ln in lines]


# ------------------------------------------------------------------- printed
def printed_from_invoice(inv: Any) -> dict[str, Any]:
    """What adapter A read, from the reviewed canonical invoice (non-tax lines only: tax lines are made
    by the tax setup, not chosen)."""
    def v(f: Any) -> Any:
        return f.value

    def printed(f: Any) -> Any:
        return f.source_value if f.source_value not in (None, "") else f.value

    lines = [{"line_number": ln.line_number, "description": v(ln.description), "quantity": _s(v(ln.quantity)),
              "unit_price": _s(v(ln.unit_price)), "amount": _s(v(ln.amount)), "printed_as": ln.line_type}
             for ln in inv.lines if ln.line_type.upper() not in TAX_TYPES]
    return {"printed_from": "page", "vendor_name": v(inv.vendor_name), "vendor_tax_id": v(inv.vendor_tax_id),
            "invoice_number": v(inv.invoice_num), "invoice_date": _s(v(inv.invoice_date)),
            "due_date": _s(v(inv.due_date)), "document_type": v(inv.invoice_type),
            "currency": v(inv.currency_code), "total": _s(v(inv.invoice_amount)), "tax": _s(v(inv.tax_amount)),
            "terms_printed": printed(inv.terms_name), "store": printed(inv.location_ref), "lines": lines}


def printed_from_keyed(header: dict[str, Any], lines: list[dict[str, Any]], vendor_name: str | None) -> dict[str, Any]:
    """The values a page would show, taken from a keyed record (no invoice file yet). Keys are the
    delivery's column names (AP_INVOICES_ALL / AP_INVOICE_LINES_ALL)."""
    from aap.clientdata.readers import day, dec, num

    kept = [x for x in sorted(lines, key=lambda x: num(x.get("LINE_NUMBER")) or 0)
            if str(x.get("LINE_TYPE_LOOKUP_CODE") or "ITEM").upper() not in TAX_TYPES]
    tax = sum((dec(x.get("AMOUNT")) or Decimal(0) for x in lines
               if str(x.get("LINE_TYPE_LOOKUP_CODE") or "").upper() in TAX_TYPES), Decimal(0))
    return {"printed_from": "keyed", "vendor_name": vendor_name, "vendor_tax_id": None,
            "invoice_number": header.get("INVOICE_NUM") and str(header["INVOICE_NUM"]),
            "invoice_date": _s(day(header.get("INVOICE_DATE"))), "due_date": None,
            "document_type": "credit memo" if (dec(header.get("INVOICE_AMOUNT")) or 0) < 0 else "invoice",
            "currency": header.get("INVOICE_CURRENCY_CODE"), "total": _s(dec(header.get("INVOICE_AMOUNT"))),
            "tax": _s(tax) if tax else None, "terms_printed": None, "store": None,
            "lines": [{"line_number": num(x.get("LINE_NUMBER")), "description": x.get("DESCRIPTION"),
                       "quantity": _s(dec(x.get("QUANTITY_INVOICED"))), "unit_price": _s(dec(x.get("UNIT_PRICE"))),
                       "amount": _s(dec(x.get("AMOUNT"))), "printed_as": None} for x in kept]}


# ------------------------------------------------------------------- request
PRINTED_HEAD, PACK_HEAD = "Printed values:\n", "\n\nContext pack:\n"


def user_text(printed: dict[str, Any], pack: dict[str, Any]) -> str:
    return PRINTED_HEAD + json.dumps(printed, ensure_ascii=False) + PACK_HEAD + json.dumps(pack, ensure_ascii=False)


def parse_user_text(text: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """(printed, pack) back from a stored request (datasets, offline evaluation)."""
    if not text.startswith(PRINTED_HEAD) or PACK_HEAD not in text:
        raise ValueError("not an adapter B request")
    printed, pack = text[len(PRINTED_HEAD):].split(PACK_HEAD, 1)
    return json.loads(printed), json.loads(pack)


def decide_request(printed: dict[str, Any], pack: dict[str, Any], *, coa: CoaLayout, model: str | None = None,
                   max_tokens: int = 1500, context: dict[str, Any] | None = None) -> LlmRequest:
    schema = decide_schema(pack, line_numbers=decide_lines(printed["lines"]), coa=coa)
    return LlmRequest(task="decide", system=SYSTEM, content=[{"type": "text", "text": user_text(printed, pack)}],
                      schema=schema, model=model, max_tokens=max_tokens,
                      context={"printed": printed, "pack": pack, **(context or {})})


# -------------------------------------------------------------------- target
def target_from_keyed(header: dict[str, Any], lines: list[dict[str, Any]], dists: list[dict[str, Any]],
                      pack: dict[str, Any], *, account_of: Any, set_name: Any, terms_name: str | None,
                      liability: str | None, coa: CoaLayout) -> dict[str, Any]:
    """What AP keyed, as adapter B's answer: the training target (no labelling needed, PLAN-MODEL-JSON §2).

    `account_of(ccid)` and `set_name(set_id)` resolve the delivery's IDs the way the context pack does.
    A line keyed to several accounts answers with the largest one (the schema has one account per
    line). A keyed value that is not a candidate is answered as null: the model cannot write it, and
    the example teaches it to send that value to review."""
    from aap.clientdata.readers import dec, num

    site = pack.get("site") or {}
    lists = pack.get("lists") or {}
    candidates = set(account_candidates(pack, coa))
    sets = {s["name"] for s in pack.get("distribution_sets") or []}
    by_line: dict[int | None, list[dict[str, Any]]] = defaultdict(list)
    for x in dists:
        if (x.get("REVERSAL_FLAG") or "N") != "Y":
            by_line[num(x.get("INVOICE_LINE_NUMBER"))].append(x)
    history = {a["account"] for a in pack.get("accounts") or [] if not a.get("fallback")}

    def header_value(value: Any, allowed: list[str] | None, default: Any) -> tuple[Any, str | None]:
        if value in (None, "") or (allowed and value not in allowed):
            return None, None
        return value, "site_default" if value == default else "chosen"

    out_lines = []
    for x in sorted(lines, key=lambda y: num(y.get("LINE_NUMBER")) or 0):
        if str(x.get("LINE_TYPE_LOOKUP_CODE") or "ITEM").upper() in TAX_TYPES:
            continue
        n = num(x.get("LINE_NUMBER"))
        line_type = str(x.get("LINE_TYPE_LOOKUP_CODE") or "ITEM").upper()
        sid = num(x.get("DISTRIBUTION_SET_ID"))
        account, dist_set, basis = None, None, None
        if sid is not None and set_name(sid) in sets:
            dist_set, basis = set_name(sid), "distribution_set"
        else:
            ds = by_line.get(n) or []
            if ds:
                top = max(ds, key=lambda y: abs(dec(y.get("AMOUNT")) or 0))
                acct = account_of(top.get("DIST_CODE_COMBINATION_ID"))
                if acct in candidates:
                    account, basis = acct, "history" if acct in history else "chosen"
        out_lines.append({"line_number": n,
                          "line_type_lookup_code": line_type if line_type in (lists.get("line_types") or [line_type])
                          else None,
                          "account": account, "distribution_set": dist_set, "basis": basis})
    inv_type = header.get("INVOICE_TYPE_LOOKUP_CODE")
    values = {
        "invoice_type_lookup_code": (inv_type, "printed") if inv_type in (lists.get("invoice_types") or [inv_type])
        else (None, None),
        "terms_name": header_value(terms_name, lists.get("terms"), site.get("terms")),
        "payment_method_code": header_value(header.get("PAYMENT_METHOD_CODE"), lists.get("payment_methods"),
                                            site.get("payment_method")),
        "pay_group_lookup_code": header_value(header.get("PAY_GROUP_LOOKUP_CODE"), lists.get("pay_groups"),
                                              site.get("pay_group")),
        "liability_account": header_value(liability, [site.get("liability_account")] if site.get("liability_account")
                                          else [], site.get("liability_account")),
    }
    answer: dict[str, Any] = {k: values[k][0] for k in HEADER_FIELDS}
    answer["lines"] = out_lines
    answer["basis"] = {k: values[k][1] for k in HEADER_FIELDS}
    return answer
