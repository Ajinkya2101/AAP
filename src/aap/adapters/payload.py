"""CanonicalInvoice -> Payables Open Interface rows (InterfacePayload).

One pure function, used by the EBS adapter's `to_payload` (what is inserted into
AP_INVOICES_INTERFACE / AP_INVOICE_LINES_INTERFACE) and by the Oracle-ready JSON
(pipeline/oracle_json.py), so the JSON and what is posted cannot differ.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from aap.schemas.canonical import CanonicalInvoice

from .base import InterfaceLine, InterfacePayload

CENT = Decimal("0.01")


def line_amount(qty: Decimal | None, price: Decimal | None, amount: Decimal | None) -> Decimal | None:
    if amount is None and qty is not None and price is not None:
        return (qty * price).quantize(CENT)
    return amount


def build_payload(inv: CanonicalInvoice, *, source: str, strict: bool = True) -> InterfacePayload:
    """Header and lines as the interface expects them. Header-level tax and freight become their own
    lines (INVOICE_AMOUNT = sum of lines); a PO-paired item line carries the PO reference and no
    account (Oracle takes the PO distribution's), any other item line its GL account. Every line's
    ACCOUNTING_DATE is the header GL date (what the Workbench defaults; required on lines).

    strict=False (the Oracle-ready JSON of an invoice still in review) builds the payload even when a
    required value (supplier, invoice number, amount, ...) is missing; it is then None, and the JSON
    lists it as missing instead of failing."""
    make: Any = InterfaceLine if strict else InterfaceLine.model_construct
    lines: list[InterfaceLine] = []
    for ln in inv.lines:
        amount = line_amount(ln.quantity.value, ln.unit_price.value, ln.amount.value)
        item = ln.line_type == "ITEM"
        paired = item and ln.po_line_number.value is not None
        if amount is None and strict:
            amount = Decimal(0)
        lines.append(make(
            line_number=len(lines) + 1, line_type_lookup_code=ln.line_type, amount=amount,
            description=(ln.description.value or "")[:240] or None,
            quantity_invoiced=ln.quantity.value if item else None,
            unit_price=ln.unit_price.value if item else None,
            unit_of_meas_lookup_code=ln.uom.value if item else None,
            po_number=(ln.po_number.value or inv.po_number.value) if paired else None,
            release_num=(ln.po_release_num.value or inv.po_release_num.value) if paired else None,
            po_line_number=ln.po_line_number.value if paired else None,
            dist_code_concatenated=None if paired else ln.dist_account.value,
            tax_rate_code=ln.tax_rate_code.value, accounting_date=inv.gl_date.value))
    for kind, value in (("TAX", inv.tax_amount.value), ("FREIGHT", inv.freight_amount.value)):
        if value and not any(x.line_type_lookup_code == kind for x in lines):
            lines.append(make(line_number=len(lines) + 1, line_type_lookup_code=kind, amount=value,
                                       description=kind.title(), accounting_date=inv.gl_date.value))
    amount = inv.invoice_amount.value
    foreign = bool(inv.ledger_currency.value) and inv.currency_code.value != inv.ledger_currency.value
    inv_type = "CREDIT" if amount is not None and amount < 0 else (inv.invoice_type.value or "STANDARD")
    header: dict[str, Any] = dict(
        invoice_num=inv.invoice_num.value, invoice_type_lookup_code=inv_type,
        invoice_date=inv.invoice_date.value, gl_date=inv.gl_date.value, vendor_id=inv.vendor_id.value,
        vendor_num=inv.vendor_number.value, vendor_name=inv.vendor_name.value,
        vendor_site_id=inv.vendor_site_id.value, vendor_site_code=inv.vendor_site_code.value,
        invoice_amount=amount, invoice_currency_code=inv.currency_code.value,
        payment_currency_code=inv.payment_currency_code.value,
        exchange_rate_type=(inv.exchange_rate_type.value or "Corporate") if foreign else None,
        exchange_date=(inv.gl_date.value or inv.invoice_date.value) if foreign else None,
        terms_id=inv.terms_id.value, terms_name=None if inv.terms_id.value else inv.terms_name.value,
        terms_date=inv.terms_date.value, payment_method_code=inv.payment_method.value,
        pay_group_lookup_code=inv.pay_group.value,
        po_number=inv.po_number.value, description=(inv.description.value or "")[:240] or None,
        source=source, org_id=inv.org_id.value, operating_unit=inv.operating_unit.value)
    if strict:
        return InterfacePayload(**header, lines=lines)
    return InterfacePayload.model_construct(**header, lines=lines)
