"""ERP adapter ports (ARCHITECTURE 9.1, plan section 5).

Only adapters know Oracle tables and APIs (R-06). The pipeline, the agents and
the reference sync call these protocols; `ADAPTER=ebs_mock` selects the mock
implementation, and a real EBS / Fusion adapter drops in later.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel
from pydantic import Field as PydField


# ------------------------------------------------------------ data types
class ReferenceData(BaseModel):
    operating_units: list[dict[str, Any]] = PydField(default_factory=list)
    suppliers: list[dict[str, Any]] = PydField(default_factory=list)
    supplier_sites: list[dict[str, Any]] = PydField(default_factory=list)
    supplier_banks: list[dict[str, Any]] = PydField(default_factory=list)
    terms: list[dict[str, Any]] = PydField(default_factory=list)
    currencies: list[dict[str, Any]] = PydField(default_factory=list)
    ccids: list[dict[str, Any]] = PydField(default_factory=list)
    categories: list[dict[str, Any]] = PydField(default_factory=list)
    uoms: list[dict[str, Any]] = PydField(default_factory=list)
    tax_rates: list[dict[str, Any]] = PydField(default_factory=list)
    tolerances: list[dict[str, Any]] = PydField(default_factory=list)
    gl_periods: list[dict[str, Any]] = PydField(default_factory=list)

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.__dict__.items()}


class PoShipment(BaseModel):
    line_location_id: int
    shipment_num: int
    quantity: Decimal
    quantity_received: Decimal
    quantity_billed: Decimal
    quantity_cancelled: Decimal
    price_override: Decimal
    receipt_required: bool
    match_option: str | None
    closed_code: str | None
    need_by_date: date | None
    po_distribution_id: int | None
    ccid: int | None
    ccid_concatenated: str | None
    po_release_id: int | None = None          # blanket release shipment
    release_num: int | None = None


class PoLine(BaseModel):
    po_line_id: int
    line_num: int
    item_id: int | None
    item_number: str | None
    item_description: str | None
    category_id: int | None
    uom: str | None
    unit_price: Decimal
    quantity: Decimal | None
    closed_code: str | None
    cancelled: bool
    shipments: list[PoShipment] = PydField(default_factory=list)

    @property
    def ordered(self) -> Decimal:
        return sum((s.quantity - s.quantity_cancelled for s in self.shipments), Decimal(0))

    @property
    def received(self) -> Decimal:
        return sum((s.quantity_received for s in self.shipments), Decimal(0))

    @property
    def billed(self) -> Decimal:
        return sum((s.quantity_billed for s in self.shipments), Decimal(0))


class PoRelease(BaseModel):
    po_release_id: int
    release_num: int
    authorization_status: str | None
    closed_code: str | None
    cancelled: bool

    @property
    def open_for_invoice(self) -> bool:
        return (self.authorization_status == "APPROVED" and not self.cancelled
                and (self.closed_code or "OPEN") not in ("CLOSED FOR INVOICE", "FINALLY CLOSED", "CLOSED"))


class PoDetail(BaseModel):
    po_header_id: int
    po_number: str
    type: str
    org_id: int
    vendor_id: int
    vendor_site_id: int
    currency_code: str
    authorization_status: str | None
    closed_code: str | None
    cancelled: bool
    buyer_name: str | None = None
    buyer_email: str | None = None
    lines: list[PoLine] = PydField(default_factory=list)
    releases: list[PoRelease] = PydField(default_factory=list)      # blanket agreements
    release_num: int | None = None                                   # set by for_release()

    @property
    def blanket(self) -> bool:
        return self.type == "BLANKET"

    @property
    def reference(self) -> str:
        """How EBS prints a release: 4500210-2."""
        return f"{self.po_number}-{self.release_num}" if self.release_num is not None else self.po_number

    def release(self, release_num: int) -> PoRelease | None:
        return next((r for r in self.releases if r.release_num == release_num), None)

    def for_release(self, release_num: int) -> PoDetail:
        """The agreement seen through one release: each line keeps only that release's shipments
        (lines the release does not ship are left out), so ordered / received / billed are the release's."""
        lines = []
        for ln in self.lines:
            ships = [s for s in ln.shipments if s.release_num == release_num]
            if ships:
                lines.append(ln.model_copy(update={"shipments": ships}))
        return self.model_copy(update={"lines": lines, "release_num": release_num})

    @property
    def open_for_invoice(self) -> bool:
        if not (self.authorization_status == "APPROVED" and not self.cancelled
                and (self.closed_code or "OPEN") not in ("CLOSED FOR INVOICE", "FINALLY CLOSED", "CLOSED")):
            return False
        rel = self.release(self.release_num) if self.release_num is not None else None
        return rel is None or rel.open_for_invoice

    @property
    def open_amount(self) -> Decimal:
        return sum(((ln.ordered - ln.billed) * ln.unit_price for ln in self.lines if not ln.cancelled), Decimal(0))


class PoSummary(BaseModel):
    po_header_id: int
    po_number: str
    vendor_site_id: int
    currency_code: str
    open_amount: Decimal
    matching_prices: list[Decimal] = PydField(default_factory=list)


class Receipt(BaseModel):
    transaction_id: int
    parent_transaction_id: int | None
    transaction_type: str
    transaction_date: date
    quantity: Decimal
    po_line_location_id: int
    receipt_num: str | None


class DuplicateHit(BaseModel):
    invoice_id: int
    invoice_num: str
    vendor_id: int
    org_id: int
    invoice_date: date
    invoice_amount: Decimal
    reason: str                     # same_number | same_amount_and_date


class CcidCheck(BaseModel):
    ccid: int | None
    concatenated: str
    exists: bool
    enabled: bool
    postable: bool
    active_on_date: bool

    @property
    def valid(self) -> bool:
        return self.exists and self.enabled and self.postable and self.active_on_date


class GlHistory(BaseModel):
    invoice_distribution_id: int
    vendor_id: int
    org_id: int
    description: str | None
    ccid: int
    concatenated: str
    natural_account: str
    amount: Decimal
    invoice_date: date
    category_code: str | None = None


class SupplierContacts(BaseModel):
    vendor_site_id: int
    vendor_id: int
    emails: list[str]
    remittance_email: str | None
    buyer_emails: list[str] = PydField(default_factory=list)


# ----------------------------------------------------------------- ports
class ErpReadPort(Protocol):
    def load_reference_data(self) -> ReferenceData: ...
    def get_po(self, po_number: str, org_id: int | None = None) -> PoDetail | None: ...
    def search_open_pos(self, vendor_id: int, currency: str, min_open_amount: Decimal,
                        unit_prices: list[Decimal]) -> list[PoSummary]: ...
    def get_receipts(self, po_line_location_ids: list[int]) -> list[Receipt]: ...
    def find_duplicate(self, vendor_id: int, invoice_num: str, amount: Decimal | None = None,
                       invoice_date: date | None = None) -> list[DuplicateHit]: ...
    def ccid_valid(self, segments: str | list[str] | int, on_date: date | None = None) -> CcidCheck: ...
    def get_gl_history(self, vendor_id: int | None = None, since: date | None = None) -> Iterator[GlHistory]: ...
    def get_supplier_contacts(self, vendor_site_id: int) -> SupplierContacts | None: ...
    def get_exchange_rate(self, from_currency: str, to_currency: str, on_date: date,
                          rate_type: str = "Corporate") -> Decimal | None: ...
    def period_status(self, ledger_id: int, on_date: date) -> tuple[str, str] | None: ...


class SubmitRef(BaseModel):
    idempotency_key: str
    group_id: str
    interface_invoice_id: int
    request_id: int | None


class SubmitStatus(BaseModel):
    state: str                      # pending | created | error
    oracle_invoice_id: int | None = None
    errors: list[str] = PydField(default_factory=list)
    holds: list[str] = PydField(default_factory=list)       # hold codes Oracle placed (created)
    detail: str | None = None                                # concurrent request completion text


class InterfaceLine(BaseModel):
    """One AP_INVOICE_LINES_INTERFACE row (EBS column names)."""
    line_number: int
    line_type_lookup_code: str = "ITEM"
    amount: Decimal
    description: str | None = None
    quantity_invoiced: Decimal | None = None
    unit_price: Decimal | None = None
    unit_of_meas_lookup_code: str | None = None
    po_number: str | None = None
    release_num: int | None = None
    po_line_number: int | None = None
    dist_code_concatenated: str | None = None
    tax_rate_code: str | None = None
    accounting_date: date | None = None


class InterfacePayload(BaseModel):
    """One AP_INVOICES_INTERFACE row + its lines (Payables Open Interface)."""
    invoice_num: str
    invoice_type_lookup_code: str = "STANDARD"
    invoice_date: date
    gl_date: date | None = None
    vendor_id: int
    vendor_site_id: int | None = None
    vendor_site_code: str | None = None
    invoice_amount: Decimal
    invoice_currency_code: str
    exchange_rate_type: str | None = None
    exchange_date: date | None = None
    terms_id: int | None = None
    terms_name: str | None = None
    po_number: str | None = None
    description: str | None = None
    source: str
    org_id: int
    lines: list[InterfaceLine]


class ErpWritePort(Protocol):
    def to_payload(self, invoice: Any) -> Any: ...
    def submit(self, payload: Any, idempotency_key: str) -> SubmitRef: ...
    def get_status(self, ref: SubmitRef) -> SubmitStatus: ...
    def apply_hold(self, oracle_invoice_id: int, hold_code: str, reason: str) -> None: ...
