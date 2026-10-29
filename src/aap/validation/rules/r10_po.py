"""V-PO purchase order checks (live via the adapter, R-06).

- Every PO the invoice names (the header PO and the POs printed on lines): it must exist in
  the invoice OU, belong to the supplier, use the invoice currency and be open for invoicing
  (S10: CLOSED FOR INVOICE -> fail). A blanket agreement is invoiced through a release: a
  printed release must exist and be open.
- No PO from a PO_REQUIRED supplier: warning; the matching agent searches or asks (S3).
"""

from __future__ import annotations

from ..engine import FAIL, PASS, SKIP, WARN, RuleContext, RuleResult, result, rule

NAME = "Purchase order"


@rule("V-PO", NAME)
def purchase_order(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    supplier = ctx.ref.suppliers.get(inv.vendor_id.value) if inv.vendor_id.value else None
    refs = inv.po_refs()
    if not refs:
        if supplier is not None and supplier.po_required:
            return [result("V-PO", NAME, WARN, "no PO number on the invoice and the supplier requires a PO",
                           "po_number", po_missing=True)]
        return [result("V-PO", NAME, SKIP if supplier is None else PASS, "PO not required")]
    if ctx.adapter is None:
        return [result("V-PO", NAME, WARN, "Oracle PO lookup not available", "po_number")]
    out = []
    for po_number, release in refs:
        label = f"{po_number}-{release}" if release is not None else po_number
        po = ctx.adapter.get_po(po_number, inv.org_id.value)
        if po is None:
            out.append(result("V-PO", NAME, FAIL, f"PO {po_number} not found in the operating unit", "po_number"))
            continue
        problems = []
        if supplier is not None and po.vendor_id != supplier.vendor_id:
            problems.append(f"PO {po_number} belongs to another supplier")
        if inv.currency_code.value and po.currency_code != inv.currency_code.value:
            problems.append(f"PO currency {po.currency_code} differs from invoice currency {inv.currency_code.value}")
        if not po.open_for_invoice:
            state = "cancelled" if po.cancelled else (po.closed_code or po.authorization_status)
            problems.append(f"PO {po_number} is not open for invoicing ({state})")
        if po.blanket and release is not None:
            rel = po.release(release)
            if rel is None:
                problems.append(f"blanket agreement {po_number} has no release {release}")
            elif not rel.open_for_invoice:
                state = "cancelled" if rel.cancelled else (rel.closed_code or rel.authorization_status)
                problems.append(f"release {label} is not open for invoicing ({state})")
        if problems:
            out.append(result("V-PO", NAME, FAIL, "; ".join(problems), "po_number", po_header_id=po.po_header_id))
            continue
        if po.blanket and release is None:
            out.append(result("V-PO", NAME, WARN, f"PO {po_number} is a blanket agreement and no release is printed",
                              "po_number", po_header_id=po.po_header_id))
            continue
        lines = len(po.for_release(release).lines) if po.blanket else len(po.lines)
        out.append(result("V-PO", NAME, PASS, f"PO {label} open, {lines} line(s)", po_header_id=po.po_header_id))
    return out
