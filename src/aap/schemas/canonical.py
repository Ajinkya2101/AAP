"""CanonicalInvoice: normalized values plus Oracle values (stages 6-7 output).

The core of the application only knows this model (R-06). The EBS adapter turns
it into interface rows; a Fusion adapter would turn it into REST payloads.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel
from pydantic import Field as PydField

from .fields import Field


def _f(t: Any) -> Any:
    return PydField(default_factory=Field[t])


class CanonicalLine(BaseModel):
    line_number: int
    line_type: str = "ITEM"                 # ITEM | TAX | FREIGHT | MISCELLANEOUS
    printed: bool = True                    # False: built at stage 7 from a header amount (tax, freight)
    description: Field[str] = _f(str)
    item_code: Field[str] = _f(str)
    quantity: Field[Decimal] = _f(Decimal)
    uom: Field[str] = _f(str)               # EBS UNIT_OF_MEASURE (e.g. "Each")
    unit_price: Field[Decimal] = _f(Decimal)
    amount: Field[Decimal] = _f(Decimal)
    tax_rate: Field[Decimal] = _f(Decimal)
    tax_rate_code: Field[str] = _f(str)     # ZX_RATES_B.TAX_RATE_CODE (TAX lines)
    category_code: Field[str] = _f(str)     # e.g. IT-CLD-INF-001
    category_id: Field[int] = _f(int)
    po_number: Field[str] = _f(str)
    po_line_number: Field[int] = _f(int)
    dist_account: Field[str] = _f(str)      # GL account (concatenated segments) for non-PO lines


class CanonicalTax(BaseModel):
    """One printed tax row (e.g. GST and QST on a Quebec invoice). tax_amount is their sum."""
    tax_no: int
    label: Field[str] = _f(str)
    rate: Field[Decimal] = _f(Decimal)
    amount: Field[Decimal] = _f(Decimal)


class CanonicalInvoice(BaseModel):
    # document / type
    invoice_type: Field[str] = _f(str)
    source: Field[str] = _f(str)
    # organization
    org_id: Field[int] = _f(int)
    operating_unit: Field[str] = _f(str)
    ledger_currency: Field[str] = _f(str)
    # supplier
    vendor_name: Field[str] = _f(str)
    vendor_id: Field[int] = _f(int)
    vendor_number: Field[str] = _f(str)
    vendor_site_id: Field[int] = _f(int)
    vendor_site_code: Field[str] = _f(str)
    vendor_tax_id: Field[str] = _f(str)
    vendor_email: Field[str] = _f(str)
    vendor_address: Field[str] = _f(str)
    # customer location (store / branch) and the GL cost center it maps to
    location_ref: Field[str] = _f(str)
    cost_center: Field[str] = _f(str)
    # header
    invoice_num: Field[str] = _f(str)
    invoice_date: Field[date] = _f(date)
    gl_date: Field[date] = _f(date)
    due_date: Field[date] = _f(date)
    currency_code: Field[str] = _f(str)
    exchange_rate_type: Field[str] = _f(str)
    terms_id: Field[int] = _f(int)
    terms_name: Field[str] = _f(str)
    po_number: Field[str] = _f(str)
    description: Field[str] = _f(str)
    # amounts
    subtotal: Field[Decimal] = _f(Decimal)
    tax_rate: Field[Decimal] = _f(Decimal)
    tax_amount: Field[Decimal] = _f(Decimal)
    taxes: list[CanonicalTax] = PydField(default_factory=list)
    freight_amount: Field[Decimal] = _f(Decimal)
    invoice_amount: Field[Decimal] = _f(Decimal)
    # payment
    bank_account: Field[str] = _f(str)
    bank_routing: Field[str] = _f(str)
    iban: Field[str] = _f(str)
    bank_change_notice: Field[str] = _f(str)
    # lines and document-level flags
    lines: list[CanonicalLine] = PydField(default_factory=list)
    flags: list[str] = PydField(default_factory=list)
    country_hint: str | None = None

    def add_flag(self, flag: str) -> None:
        if flag not in self.flags:
            self.flags.append(flag)

    def item_lines(self) -> list[CanonicalLine]:
        return [ln for ln in self.lines if ln.line_type == "ITEM"]

    def iter_fields(self):
        """Yields (path, Field) for every header and line field."""
        for name in type(self).model_fields:
            v = getattr(self, name)
            if isinstance(v, Field):
                yield name, v
        for t in self.taxes:
            for name in ("label", "rate", "amount"):
                yield f"taxes[{t.tax_no}].{name}", getattr(t, name)
        for ln in self.lines:
            for name in CanonicalLine.model_fields:
                v = getattr(ln, name)
                if isinstance(v, Field):
                    yield f"lines[{ln.line_number}].{name}", v
