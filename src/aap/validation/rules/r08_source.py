"""C12 source check: values the LLM returned must be on the page (ARCHITECTURE 6.3)."""

from __future__ import annotations

from aap.schemas.fields import Flag

from ..engine import FAIL, PASS, WARN, RuleContext, RuleResult, result, rule

KEY_FIELDS = ["invoice_num", "invoice_amount", "vendor_tax_id", "bank_account", "iban"]


@rule("C12", "Source check")
def source_check(ctx: RuleContext) -> list[RuleResult]:
    not_found = [path for path, f in ctx.inv.iter_fields() if Flag.not_found_in_source in f.flags]
    unverified = [path for path, f in ctx.inv.iter_fields() if Flag.unverified_image_only in f.flags]
    key_missing = [p for p in not_found if p in KEY_FIELDS]
    if key_missing:
        return [result("C12", "Source check", FAIL,
                       "key values not found on the page: " + ", ".join(key_missing), *key_missing)]
    if not_found:
        return [result("C12", "Source check", WARN, "values not found on the page: " + ", ".join(not_found[:10]),
                       *not_found)]
    if unverified:
        return [result("C12", "Source check", WARN,
                       "page has no text layer: values read from the image only", *unverified[:10])]
    return [result("C12", "Source check", PASS)]
