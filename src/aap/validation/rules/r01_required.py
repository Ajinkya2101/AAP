"""C1 / V-01: required fields present."""

from __future__ import annotations

from ..engine import FAIL, PASS, WARN, RuleContext, RuleResult, result, rule

REQUIRED = {
    "vendor_id": "supplier", "vendor_site_id": "supplier site", "org_id": "operating unit",
    "invoice_num": "invoice number", "invoice_date": "invoice date", "invoice_amount": "invoice total",
    "currency_code": "currency",
}
RECOMMENDED = {"terms_id": "payment terms"}


@rule("C1", "Required fields present")
def required_fields(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    missing = [label for name, label in REQUIRED.items() if not getattr(inv, name).present]
    if missing:
        return [result("C1", "Required fields present", FAIL, "Missing: " + ", ".join(missing),
                       *[n for n, label in REQUIRED.items() if label in missing])]
    if not inv.item_lines():
        return [result("C1", "Required fields present", WARN, "No invoice lines were read", "lines")]
    soft = [label for name, label in RECOMMENDED.items() if not getattr(inv, name).present]
    if soft:
        return [result("C1", "Required fields present", WARN, "Not found: " + ", ".join(soft),
                       *RECOMMENDED.keys())]
    return [result("C1", "Required fields present", PASS)]
