"""V-05 mapping complete: every Oracle value found; LLM-chosen values are marked for checking."""

from __future__ import annotations

from aap.schemas.fields import Flag

from ..engine import FAIL, PASS, WARN, RuleContext, RuleResult, result, rule

BLOCKING = {"vendor_id", "vendor_site_id", "currency_code"}


@rule("V-05", "Mapping complete")
def mapping_complete(ctx: RuleContext) -> list[RuleResult]:
    out: list[RuleResult] = []
    no_map = [path for path, f in ctx.inv.iter_fields() if Flag.no_map in f.flags]
    blocking = [p for p in no_map if p in BLOCKING]
    if blocking:
        out.append(result("V-05", "Mapping complete", FAIL, "no Oracle value for: " + ", ".join(blocking), *blocking))
    soft = [p for p in no_map if p not in BLOCKING]
    if soft:
        out.append(result("V-05", "Mapping complete", WARN, "no Oracle value for: " + ", ".join(soft[:10]), *soft))
    check = [path for path, f in ctx.inv.iter_fields() if Flag.check_map in f.flags]
    if check:
        out.append(result("V-05", "Mapping complete", WARN,
                          "chosen by the model from candidates, please check: " + ", ".join(check[:10]), *check))
    if "tax_id_name_conflict" in ctx.inv.vendor_id.flags:
        out.append(result("V-05", "Mapping complete", WARN,
                          "supplier found by tax ID but the printed name is different", "vendor_id"))
    return out or [result("V-05", "Mapping complete", PASS)]
