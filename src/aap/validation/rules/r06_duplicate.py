"""C10 / V-06 duplicate invoice: against the local data and against Oracle (live).
V-06 runs again at submission time (stage 10)."""

from __future__ import annotations

from sqlalchemy import func, select

from aap.db.models import Document, InvoiceVersion
from aap.pipeline.status import Status

from ..engine import FAIL, PASS, SKIP, WARN, RuleContext, RuleResult, result, rule

CLOSED = [Status.REJECTED.value, Status.FAILED.value, Status.DUPLICATE.value, Status.NOT_INVOICE.value]


@rule("C10", "Duplicate invoice")
def duplicate(ctx: RuleContext) -> list[RuleResult]:
    inv = ctx.inv
    vendor_id, num = inv.vendor_id.value, inv.invoice_num.value
    if not vendor_id or not num:
        return [result("C10", "Duplicate invoice", SKIP, "supplier or invoice number unknown")]
    ci = InvoiceVersion.canonical_invoice
    local = ctx.session.execute(
        select(InvoiceVersion.document_id).join(Document, Document.id == InvoiceVersion.document_id)
        .where(InvoiceVersion.document_id != ctx.document.id,
               InvoiceVersion.kind.in_(["mapped", "user", "vendor_reply"]),
               ci["vendor_id"]["value"].as_integer() == vendor_id,
               func.upper(ci["invoice_num"]["value"].as_string()) == num.upper(),
               Document.status.not_in(CLOSED),
               # Eval documents (aap.eval) only see documents of the same eval run, and vice versa.
               (Document.uploaded_by == ctx.document.uploaded_by) if ctx.document.source == "eval"
               else (Document.source != "eval"))
        .limit(1)).first()
    if local:
        return [result("C10", "Duplicate invoice", FAIL,
                       f"invoice {num} of this supplier was already received (document {local[0]})",
                       "invoice_num", duplicate_document_id=str(local[0]))]
    if ctx.adapter is None:
        return [result("C10", "Duplicate invoice", WARN, "Oracle duplicate check not available")]
    hits = ctx.adapter.find_duplicate(vendor_id, num, inv.invoice_amount.value, inv.invoice_date.value)
    same_num = [h for h in hits if h.reason == "same_number"]
    if same_num:
        h = same_num[0]
        return [result("C10", "Duplicate invoice", FAIL,
                       f"Oracle already has invoice {h.invoice_num} for this supplier (invoice id {h.invoice_id}, "
                       f"{h.invoice_date}, {h.invoice_amount})", "invoice_num", oracle_invoice_id=h.invoice_id)]
    if hits:
        h = hits[0]
        return [result("C10", "Duplicate invoice", WARN,
                       f"Oracle invoice {h.invoice_num} has the same amount and date", "invoice_amount",
                       oracle_invoice_id=h.invoice_id)]
    return [result("C10", "Duplicate invoice", PASS)]
