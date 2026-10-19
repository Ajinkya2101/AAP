"""V-PO purchase order checks (live via the adapter, R-06).

- PO printed: it must exist in the invoice OU, belong to the supplier, use the
  invoice currency and be open for invoicing (S10: CLOSED FOR INVOICE -> fail).
- No PO from a PO_REQUIRED supplier: warning; the matching agent searches or asks (S3).
"""

from __future__ import annotations

from ..engine import FAIL, PASS, SKIP, WARN, RuleContext, RuleResult, result, rule

NAME = "Purchase order"


@rule("V-PO", NAME)
def purchase_order(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    supplier = ctx.ref.suppliers.get(inv.vendor_id.value) if inv.vendor_id.value else None
    po_number = inv.po_number.value
    if not po_number:
        if supplier is not None and supplier.po_required:
            return [result("V-PO", NAME, WARN, "no PO number on the invoice and the supplier requires a PO",
                           "po_number", po_missing=True)]
        return [result("V-PO", NAME, SKIP if supplier is None else PASS, "PO not required")]
    if ctx.adapter is None:
        return [result("V-PO", NAME, WARN, "Oracle PO lookup not available", "po_number")]
    po = ctx.adapter.get_po(po_number, inv.org_id.value)
    if po is None:
        return [result("V-PO", NAME, FAIL, f"PO {po_number} not found in the operating unit", "po_number")]
    problems = []
    if supplier is not None and po.vendor_id != supplier.vendor_id:
        problems.append(f"PO {po_number} belongs to another supplier")
    if inv.currency_code.value and po.currency_code != inv.currency_code.value:
        problems.append(f"PO currency {po.currency_code} differs from invoice currency {inv.currency_code.value}")
    if not po.open_for_invoice:
        state = "cancelled" if po.cancelled else (po.closed_code or po.authorization_status)
        problems.append(f"PO {po_number} is not open for invoicing ({state})")
    if problems:
        return [result("V-PO", NAME, FAIL, "; ".join(problems), "po_number", po_header_id=po.po_header_id)]
    return [result("V-PO", NAME, PASS, f"PO {po_number} open, {len(po.lines)} line(s)",
                   po_header_id=po.po_header_id)]
