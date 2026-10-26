"""C13 invoice type matches the sign of the total: a credit memo is negative in Payables,
a standard invoice is not."""

from __future__ import annotations

from ..engine import FAIL, PASS, SKIP, RuleContext, RuleResult, result, rule


@rule("C13", "Invoice type and amount sign")
def invoice_type_sign(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    kind, total = inv.invoice_type.value or "STANDARD", inv.invoice_amount.value
    if total is None or total == 0:
        return [result("C13", "Invoice type and amount sign", SKIP, "no total")]
    if kind == "CREDIT" and total > 0:
        return [result("C13", "Invoice type and amount sign", FAIL,
                       f"credit memo with a positive total {total}", "invoice_type", "invoice_amount")]
    if kind == "STANDARD" and total < 0:
        return [result("C13", "Invoice type and amount sign", FAIL,
                       f"standard invoice with a negative total {total}: is it a credit memo?",
                       "invoice_type", "invoice_amount")]
    return [result("C13", "Invoice type and amount sign", PASS, kind)]
