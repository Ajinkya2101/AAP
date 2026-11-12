"""The per-invoice answer schema of adapter B (PLAN-MODEL-JSON §2, §5): the Oracle fields the model
writes, each restricted to the candidates of this invoice's context pack, with the `basis` of each
value.

    schema = decide_schema(pack, line_numbers=[1, 2], coa=layout)

Constrained output is what makes the model safe with little data: it cannot write an account, set,
term or type that is not a candidate (vLLM json_schema, strict). `null` is always allowed and means
"none of the candidates fits": that value goes to review.

| Field | Candidates |
|---|---|
| invoice_type_lookup_code, lines[].line_type_lookup_code | the instance's enabled invoice / line types |
| terms_name, payment_method_code, pay_group_lookup_code | the instance's enabled values (the site default is in the pack; the code compares the answer with it) |
| liability_account | the site's liability account |
| lines[].account | the pack's accounts, plus each of them with the cost center of a printed store (store table) |
| lines[].distribution_set | the sets in the pack |

`basis`: printed | site_default | history | distribution_set | chosen. It tells the reviewer and the
eval where a value came from, so a reading error, a wrong default and a wrong choice stay apart.
"""

from __future__ import annotations

from typing import Any

from aap.common.coa import CoaLayout

SCHEMA_VERSION = "decide/1"
BASIS = ["printed", "site_default", "history", "distribution_set", "chosen"]
HEADER_FIELDS = ("invoice_type_lookup_code", "terms_name", "payment_method_code", "pay_group_lookup_code",
                 "liability_account")


def _enum(values: list[Any]) -> dict[str, Any]:
    """One of `values`, or null."""
    real = list(dict.fromkeys(v for v in values if v not in (None, "")))
    return {"enum": [*real, None]}


def account_candidates(pack: dict[str, Any], coa: CoaLayout) -> list[str]:
    """The pack's accounts, then each of them with the cost center of every store printed on the invoice
    (the store table says where the cost lands; history gives the natural account)."""
    accounts = [a["account"] for a in pack.get("accounts") or [] if a.get("account")]
    for s in pack.get("distribution_sets") or []:
        accounts += [a["account"] for a in s.get("keyed_accounts") or [] if a.get("account")]
    out = list(dict.fromkeys(accounts))
    for store in pack.get("stores") or []:
        for a in list(out):
            parts = coa.split(a)
            if parts and coa.cost_center_segment <= len(parts):
                parts[coa.cost_center_segment - 1] = store["cost_center"]
                out.append(coa.join(parts))
    return list(dict.fromkeys(out))


def decide_schema(pack: dict[str, Any], *, line_numbers: list[int], coa: CoaLayout) -> dict[str, Any]:
    lists = pack.get("lists") or {}
    site = pack.get("site") or {}
    sets = [s["name"] for s in pack.get("distribution_sets") or [] if s.get("name")]
    basis = {"enum": [*BASIS, None]}
    line = {
        "type": "object", "additionalProperties": False,
        "required": ["line_number", "line_type_lookup_code", "account", "distribution_set", "basis"],
        "properties": {
            "line_number": {"enum": list(line_numbers)},
            "line_type_lookup_code": _enum(lists.get("line_types") or ["ITEM", "FREIGHT", "MISCELLANEOUS"]),
            "account": _enum(account_candidates(pack, coa)),
            "distribution_set": _enum(sets),
            "basis": basis,
        },
    }
    header_basis = {"type": "object", "additionalProperties": False, "required": list(HEADER_FIELDS),
                    "properties": {k: basis for k in HEADER_FIELDS}}
    return {
        "type": "object", "additionalProperties": False,
        "required": [*HEADER_FIELDS, "lines", "basis"],
        "properties": {
            "invoice_type_lookup_code": _enum(lists.get("invoice_types") or ["STANDARD", "CREDIT"]),
            "terms_name": _enum(lists.get("terms") or [site.get("terms")]),
            "payment_method_code": _enum(lists.get("payment_methods") or [site.get("payment_method")]),
            "pay_group_lookup_code": _enum(lists.get("pay_groups") or [site.get("pay_group")]),
            "liability_account": _enum([site.get("liability_account")]),
            "lines": {"type": "array", "items": line, "minItems": len(line_numbers), "maxItems": len(line_numbers)},
            "basis": header_basis,
        },
    }


def check_answer(answer: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Where an answer leaves the schema (the stub and offline scoring; vLLM enforces it). Empty = valid."""
    problems = []
    props = schema["properties"]
    for k in HEADER_FIELDS:
        if answer.get(k) not in props[k]["enum"]:
            problems.append(f"{k}={answer.get(k)!r} is not a candidate")
    item = props["lines"]["items"]["properties"]
    lines = answer.get("lines") or []
    if not props["lines"]["minItems"] <= len(lines) <= props["lines"]["maxItems"]:
        problems.append(f"{len(lines)} lines, expected {props['lines']['minItems']}")
    for ln in lines:
        for k in ("line_number", "line_type_lookup_code", "account", "distribution_set", "basis"):
            if ln.get(k) not in item[k]["enum"]:
                problems.append(f"line {ln.get('line_number')}: {k}={ln.get(k)!r} is not a candidate")
    for k in HEADER_FIELDS:
        if (answer.get("basis") or {}).get(k) not in [*BASIS, None]:
            problems.append(f"basis.{k} is not one of {BASIS}")
    return problems
