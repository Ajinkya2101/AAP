"""C8 / V-04: supplier and site active; site is a pay site in the invoice OU."""

from __future__ import annotations

from ..engine import FAIL, PASS, SKIP, RuleContext, RuleResult, result, rule


@rule("C8", "Supplier and site active")
def supplier_active(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    supplier = ctx.ref.suppliers.get(inv.vendor_id.value) if inv.vendor_id.value else None
    site = ctx.ref.sites.get(inv.vendor_site_id.value) if inv.vendor_site_id.value else None
    if supplier is None:
        return [result("C8", "Supplier and site active", SKIP, "supplier not mapped")]
    problems = []
    if not supplier.enabled or (supplier.end_date_active and supplier.end_date_active <= ctx.today):
        problems.append(f"supplier {supplier.vendor_name} is inactive")
    if supplier.hold_all_payments:
        problems.append("supplier is on payment hold")
    if site is None:
        return [result("C8", "Supplier and site active", FAIL if problems else SKIP,
                       "; ".join(problems) or "site not mapped", "vendor_id")]
    if site.inactive_date and site.inactive_date <= ctx.today:
        problems.append(f"site {site.vendor_site_code} is inactive since {site.inactive_date.isoformat()}")
    if not site.pay_site:
        problems.append(f"site {site.vendor_site_code} is not a pay site")
    if problems:
        return [result("C8", "Supplier and site active", FAIL, "; ".join(problems), "vendor_id", "vendor_site_id")]
    return [result("C8", "Supplier and site active", PASS)]
