"""C6 tax ID format/checksum, C7 tax ID matches the supplier master."""

from __future__ import annotations

from aap.common.taxid import classify_tax_id, is_valid_tax_id, normalize_tax_id

from ..engine import FAIL, PASS, SKIP, WARN, RuleContext, RuleResult, result, rule


@rule("C6", "Tax ID format")
def tax_id_format(ctx: RuleContext) -> list[RuleResult]:
    tax = ctx.inv.vendor_tax_id.value
    if not tax:
        return [result("C6", "Tax ID format", SKIP, "no tax ID printed")]
    kind = classify_tax_id(tax)
    if kind is None:
        return [result("C6", "Tax ID format", WARN, f"unknown tax ID format: {tax}", "vendor_tax_id")]
    if not is_valid_tax_id(tax):
        return [result("C6", "Tax ID format", FAIL, f"{kind} {tax} is not valid (format or checksum)",
                       "vendor_tax_id")]
    return [result("C6", "Tax ID format", PASS, kind)]


@rule("C7", "Tax ID matches supplier master")
def tax_id_master(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    supplier = ctx.ref.suppliers.get(inv.vendor_id.value) if inv.vendor_id.value else None
    if supplier is None:
        return [result("C7", "Tax ID matches supplier master", SKIP, "supplier not mapped")]
    printed = normalize_tax_id(inv.vendor_tax_id.value)
    master = normalize_tax_id(supplier.tax_id)
    if not printed:
        return [result("C7", "Tax ID matches supplier master", WARN, "no tax ID printed on the invoice",
                       "vendor_tax_id")]
    if not master:
        return [result("C7", "Tax ID matches supplier master", WARN, "supplier master has no tax ID")]
    if printed != master:
        return [result("C7", "Tax ID matches supplier master", FAIL,
                       f"invoice shows {printed}, supplier master has {master}", "vendor_tax_id")]
    return [result("C7", "Tax ID matches supplier master", PASS)]
