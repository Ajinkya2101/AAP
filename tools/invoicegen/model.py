"""Render model and ground truth."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Vendor:
    name: str
    address_lines: list[str]
    email: str | None = None
    phone: str | None = None
    tax_id: str | None = None


@dataclass
class Line:
    description: str
    amount: str
    quantity: str | None = None
    uom: str | None = None
    unit_price: str | None = None
    po_line_ref: str | None = None
    page: int = 1


@dataclass
class InvoiceDoc:
    key: str                              # file stem
    layout: str
    labels: dict[str, str]
    vendor: Vendor
    bill_to: list[str]
    invoice_number: str
    invoice_date: str
    lines: list[Line]
    total: str
    lang: str = "en"
    ship_to: list[str] | None = None
    due_date: str | None = None
    po_number: str | None = None
    payment_terms: str | None = None
    currency_label: str | None = None
    currency_symbol: str | None = None    # the symbol printed with amounts, if any
    subtotal: str | None = None
    tax_label: str | None = None
    tax_amount: str | None = None
    freight_amount: str | None = None
    bank_lines: list[str] = field(default_factory=list)
    bank_account: str | None = None
    bank_routing: str | None = None
    iban: str | None = None
    notes: str | None = None
    account_ref: str | None = None
    accent: str | None = None
    page_size: str = "Letter"
    scanned: bool = False
    # delivery
    deliver: str = "pdf"                  # pdf | eml
    sender: str | None = None
    email_group: str | None = None
    # expectations for tests / eval
    expected: dict[str, Any] = field(default_factory=dict)

    @property
    def has_qty(self) -> bool:
        return any(ln.quantity for ln in self.lines)

    @property
    def has_uom(self) -> bool:
        return any(ln.uom for ln in self.lines)

    def source_truth(self) -> dict[str, Any]:
        """The ExtractedInvoice a perfect model would return (values exactly as printed)."""
        rate = None
        if self.tax_label:
            m = re.search(r"\d+(?:[.,]\d+)?\s*%", self.tax_label)
            rate = m.group(0) if m else None
        currency = self.currency_label or self.currency_symbol
        return {
            "vendor_name": self.vendor.name,
            "vendor_address": ", ".join(self.vendor.address_lines) or None,
            "vendor_tax_id": self.vendor.tax_id,
            "vendor_email": self.vendor.email,
            "vendor_phone": self.vendor.phone,
            "bill_to": ", ".join(self.bill_to) or None,
            "ship_to": ", ".join(self.ship_to) if self.ship_to else None,
            "invoice_number": self.invoice_number,
            "invoice_date": self.invoice_date,
            "due_date": self.due_date,
            "po_number": self.po_number,
            "payment_terms": self.payment_terms,
            "currency": currency.strip() if currency else None,
            "subtotal": self.subtotal,
            "tax_label": self.tax_label,
            "tax_rate": rate,
            "tax_amount": self.tax_amount,
            "freight_amount": self.freight_amount,
            "total": self.total,
            "bank_account": self.bank_account,
            "bank_routing": self.bank_routing,
            "iban": self.iban,
            "remit_to": None,
            "lines": [{"description": ln.description, "item_code": None, "quantity": ln.quantity, "uom": ln.uom,
                       "unit_price": ln.unit_price, "amount": ln.amount, "tax_rate": None,
                       "po_line_ref": ln.po_line_ref, "_page": ln.page} for ln in self.lines],
        }

    def render_context(self) -> dict[str, Any]:
        ctx = asdict(self)
        ctx["vendor"] = self.vendor
        ctx["lines"] = self.lines
        ctx["has_qty"] = self.has_qty
        ctx["has_uom"] = self.has_uom
        return ctx
