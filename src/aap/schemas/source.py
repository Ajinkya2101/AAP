"""SourceInvoice: values exactly as the supplier wrote them (stage 5 output).

ExtractedInvoice is the flat JSON schema the LLM must follow (R-02, xgrammar).
Application code then wraps each value into Field[str] with page/bbox from the
source check (R-03) to build SourceInvoice.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic import Field as PydField

from .fields import Field

HEADER_FIELDS = [
    "vendor_name", "vendor_address", "vendor_tax_id", "vendor_email", "vendor_phone",
    "bill_to", "ship_to", "invoice_number", "invoice_date", "due_date", "po_number",
    "payment_terms", "currency", "subtotal", "tax_label", "tax_rate", "tax_amount",
    "freight_amount", "total", "bank_account", "bank_routing", "iban", "remit_to",
]
LINE_FIELDS = ["description", "item_code", "quantity", "uom", "unit_price", "amount", "tax_rate", "po_line_ref"]


# ------------------------------------------------------------- LLM output
class ExtractedLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str | None
    item_code: str | None
    quantity: str | None
    uom: str | None
    unit_price: str | None
    amount: str | None
    tax_rate: str | None
    po_line_ref: str | None


class ExtractedInvoice(BaseModel):
    """Every key is required and nullable, so the model always answers every field."""

    model_config = ConfigDict(extra="forbid")

    vendor_name: str | None
    vendor_address: str | None
    vendor_tax_id: str | None
    vendor_email: str | None
    vendor_phone: str | None
    bill_to: str | None
    ship_to: str | None
    invoice_number: str | None
    invoice_date: str | None
    due_date: str | None
    po_number: str | None
    payment_terms: str | None
    currency: str | None
    subtotal: str | None
    tax_label: str | None
    tax_rate: str | None
    tax_amount: str | None
    freight_amount: str | None
    total: str | None
    bank_account: str | None
    bank_routing: str | None
    iban: str | None
    remit_to: str | None
    lines: list[ExtractedLine]


# Header fields that can carry evidence (everything except the line list).
EVIDENCE_FIELDS = tuple(f for f in ExtractedInvoice.model_fields if f != "lines")
EvidenceField = Literal[EVIDENCE_FIELDS]  # type: ignore[valid-type]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: EvidenceField
    page: int
    bbox: list[int] = PydField(min_length=4, max_length=4)


class ExtractedInvoiceGrounded(ExtractedInvoice):
    """Image-only pages: the model also returns where it read each header value.
    The schema bounds the list (header field names only, at most one entry per field):
    unbounded, the model looped on "lines" boxes until max_tokens (S13, eval 2026-09-29)."""

    evidence: list[Evidence] = PydField(max_length=len(EVIDENCE_FIELDS))


# ---------------------------------------------------------- SourceInvoice
class SourceLine(BaseModel):
    line_no: int
    description: Field[str] = PydField(default_factory=Field[str])
    item_code: Field[str] = PydField(default_factory=Field[str])
    quantity: Field[str] = PydField(default_factory=Field[str])
    uom: Field[str] = PydField(default_factory=Field[str])
    unit_price: Field[str] = PydField(default_factory=Field[str])
    amount: Field[str] = PydField(default_factory=Field[str])
    tax_rate: Field[str] = PydField(default_factory=Field[str])
    po_line_ref: Field[str] = PydField(default_factory=Field[str])


class SourceInvoice(BaseModel):
    vendor_name: Field[str] = PydField(default_factory=Field[str])
    vendor_address: Field[str] = PydField(default_factory=Field[str])
    vendor_tax_id: Field[str] = PydField(default_factory=Field[str])
    vendor_email: Field[str] = PydField(default_factory=Field[str])
    vendor_phone: Field[str] = PydField(default_factory=Field[str])
    bill_to: Field[str] = PydField(default_factory=Field[str])
    ship_to: Field[str] = PydField(default_factory=Field[str])
    invoice_number: Field[str] = PydField(default_factory=Field[str])
    invoice_date: Field[str] = PydField(default_factory=Field[str])
    due_date: Field[str] = PydField(default_factory=Field[str])
    po_number: Field[str] = PydField(default_factory=Field[str])
    payment_terms: Field[str] = PydField(default_factory=Field[str])
    currency: Field[str] = PydField(default_factory=Field[str])
    subtotal: Field[str] = PydField(default_factory=Field[str])
    tax_label: Field[str] = PydField(default_factory=Field[str])
    tax_rate: Field[str] = PydField(default_factory=Field[str])
    tax_amount: Field[str] = PydField(default_factory=Field[str])
    freight_amount: Field[str] = PydField(default_factory=Field[str])
    total: Field[str] = PydField(default_factory=Field[str])
    bank_account: Field[str] = PydField(default_factory=Field[str])
    bank_routing: Field[str] = PydField(default_factory=Field[str])
    iban: Field[str] = PydField(default_factory=Field[str])
    remit_to: Field[str] = PydField(default_factory=Field[str])
    lines: list[SourceLine] = PydField(default_factory=list)
    flags: list[str] = PydField(default_factory=list)

    @classmethod
    def from_extracted(cls, ex: ExtractedInvoice) -> SourceInvoice:
        data: dict = {}
        for name in HEADER_FIELDS:
            v = getattr(ex, name)
            data[name] = Field[str](value=_clean(v), source_value=v)
        lines = []
        for i, ln in enumerate(ex.lines, start=1):
            ld: dict = {"line_no": i}
            for name in LINE_FIELDS:
                v = getattr(ln, name)
                ld[name] = Field[str](value=_clean(v), source_value=v)
            lines.append(SourceLine(**ld))
        data["lines"] = lines
        return cls(**data)


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = " ".join(v.split())
    if v.lower() == "null":     # the model wrote the word instead of JSON null (eval 2026-09-29)
        return None
    return v or None
