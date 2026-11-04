"""V-LOV: the invoice type, every line type and the invoice source are codes the instance accepts
(IMPLEMENTATION-PLAN-CLIENT-DATA P3.3).

Checked against the instance's own lists (FND_LOOKUP_VALUES -> ref_lookup, common/lookups.py), on
the codes the interface rows will carry (adapters/payload.py: header tax and freight become their
own lines). The Payables import rejects a code that is not in the list or is disabled, so this is a
fail. A lookup type the reference sync found no rows for is not checked (an instance whose list
was not delivered yet); the result says which.
"""

from __future__ import annotations

from aap.adapters.payload import build_payload
from aap.common.lookups import INVOICE_TYPE, LINE_TYPE, SOURCE
from aap.config import get_settings

from ..engine import FAIL, PASS, SKIP, RuleContext, RuleResult, result, rule

NAME = "Codes in the instance's lists"


@rule("V-LOV", NAME)
def lookup_codes(ctx: RuleContext) -> list[RuleResult]:
    lists = ctx.ref.lookups
    if not lists:
        return [result("V-LOV", NAME, SKIP, "no Payables lookups in the reference data (reference sync)")]
    p = build_payload(ctx.inv, source=get_settings().invoice_source, strict=False)
    checks: list[tuple[str, str | None, str, str]] = [
        (INVOICE_TYPE, p.invoice_type_lookup_code, "invoice type", "invoice_type"),
        (SOURCE, p.source, "invoice source", "source")]
    for n, ln in enumerate(p.lines, start=1):
        if n <= len(ctx.inv.lines):         # payload lines follow the invoice's lines, then header tax / freight
            path = f"lines[{ctx.inv.lines[n - 1].line_number}].line_type"
        else:
            path = "tax_amount" if ln.line_type_lookup_code == "TAX" else "freight_amount"
        checks.append((LINE_TYPE, ln.line_type_lookup_code, f"line {n} type", path))
    bad, paths, unchecked, ok = [], [], set(), []
    for lookup_type, code, label, path in checks:
        codes = lists.get(lookup_type)
        if not codes:
            unchecked.add(lookup_type)
            continue
        row = codes.get(code or "")
        if row is None:
            bad.append(f"{label} {code!r} is not in the instance's {lookup_type} list")
        elif not row.enabled:
            bad.append(f"{label} {code!r} is disabled in the instance's {lookup_type} list")
        else:
            ok.append(f"{label} {code}")
            continue
        paths.append(path)
    note = f" (not checked, no list: {', '.join(sorted(unchecked))})" if unchecked else ""
    if bad:
        return [result("V-LOV", NAME, FAIL, "; ".join(dict.fromkeys(bad)) + note, *dict.fromkeys(paths))]
    if not ok:
        return [result("V-LOV", NAME, SKIP, "no list for these lookup types" + note)]
    return [result("V-LOV", NAME, PASS, ", ".join(dict.fromkeys(ok)) + note)]
