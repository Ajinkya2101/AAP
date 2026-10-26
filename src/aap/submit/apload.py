"""APLOAD file: the customer's existing AP interface format (sample APILOAD_20251118.csv from the
functional team, 2026-10-23), as an alternative to writing the open-interface tables directly.

Pipe-delimited, every row ends with "|", LF line ends. Two column rows first, then per invoice one
H row and its L rows:

    H|InvoiceNumber|VendorNumber|SiteName|PONumber|ReleaseNumber|InvoiceDate|InvoiceAmount|InvoiceSource|TaxAmount|
    L|InvoiceNumber|VendorNumber|SiteName|LineTypeLookupCode|POLineNumber|POShipmentNumber|InvoiceLineNumber|
      InvoicedQuantity|UnitPrice|BuyerItemNumber|Distribution|

Dates MMDDYYYY, header amounts with 5 decimals. Tax is the header TaxAmount, not a line: the
sample's lines add up to InvoiceAmount - TaxAmount. A line has no amount column, so quantity x
unit price must give the line amount; a line without both is written as 1 x amount (as the sample
does for every line).

    python -m aap.submit.apload --out /data/filestore/apload/APILOAD_20261026.csv [--document <id> ...]
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from aap.config import get_settings
from aap.schemas.canonical import CanonicalInvoice, CanonicalLine

HEADER_COLUMNS = ["InvoiceNumber", "VendorNumber", "SiteName", "PONumber", "ReleaseNumber", "InvoiceDate",
                  "InvoiceAmount", "InvoiceSource", "TaxAmount"]
LINE_COLUMNS = ["InvoiceNumber", "VendorNumber", "SiteName", "LineTypeLookupCode", "POLineNumber", "POShipmentNumber",
                "InvoiceLineNumber", "InvoicedQuantity", "UnitPrice", "BuyerItemNumber", "Distribution"]
CENT = Decimal("0.01")


class AploadError(ValueError):
    pass


def _row(kind: str, values: Iterable[str]) -> str:
    return "|".join([kind, *(v.replace("|", " ") for v in values)]) + "|"


def _amount5(value: Decimal | None) -> str:
    return "" if value is None else f"{Decimal(value):.5f}"


def _number(value: Decimal) -> str:
    """Plain decimal without exponent or trailing zeros: 1, 16408, 0.05621."""
    return format(Decimal(value).normalize(), "f")


def _price(value: Decimal) -> str:
    value = Decimal(value)
    return f"{value:.2f}" if value == value.quantize(CENT) else _number(value)


def _qty_price(ln: CanonicalLine) -> tuple[str, str]:
    qty, price, amount = ln.quantity.value, ln.unit_price.value, ln.amount.value
    if amount is None:
        raise AploadError(f"line {ln.line_number} has no amount")
    if qty is not None and price is not None and (Decimal(qty) * Decimal(price)).quantize(CENT) == Decimal(amount):
        return _number(qty), _price(price)
    return "1", _price(amount)


def invoice_rows(inv: CanonicalInvoice, source: str | None = None) -> list[str]:
    """H row + L rows of one approved invoice."""
    missing = [label for label, f in (("invoice number", inv.invoice_num), ("supplier number", inv.vendor_number),
                                      ("supplier site", inv.vendor_site_code), ("invoice date", inv.invoice_date),
                                      ("invoice total", inv.invoice_amount)) if not f.present]
    if missing:
        raise AploadError("cannot write APLOAD rows, missing: " + ", ".join(missing))
    num, vendor, site = inv.invoice_num.value, inv.vendor_number.value, inv.vendor_site_code.value
    lines = [ln for ln in inv.lines if ln.line_type != "TAX"]
    paired = any(ln.po_line_number.value is not None for ln in lines if ln.line_type == "ITEM")
    tax = sum((ln.amount.value or Decimal(0) for ln in inv.lines if ln.line_type == "TAX"), Decimal(0))
    rows = [_row("H", [num, vendor, site, (inv.po_number.value or "") if paired else "", "",
                       f"{inv.invoice_date.value:%m%d%Y}", _amount5(inv.invoice_amount.value),
                       source or get_settings().invoice_source, _amount5(tax)])]
    for n, ln in enumerate(lines, start=1):
        qty, price = _qty_price(ln)
        po_line = ln.po_line_number.value if ln.line_type == "ITEM" else None
        rows.append(_row("L", [num, vendor, site, ln.line_type, str(po_line) if po_line is not None else "", "",
                               str(n), qty, price, "", "" if po_line is not None else (ln.dist_account.value or "")]))
    return rows


def apload_text(invoices: Iterable[CanonicalInvoice], source: str | None = None) -> str:
    rows = [_row("H", HEADER_COLUMNS), _row("L", LINE_COLUMNS)]
    for inv in invoices:
        rows.extend(invoice_rows(inv, source))
    return "\n".join(rows) + "\n"


def document_invoice(session: Session, document_id: uuid.UUID) -> tuple[CanonicalInvoice, bool]:
    """The approved version of a document, or (not approved yet) the version a clerk would approve now,
    with the agents' PO pairing and GL accounts. Returns (invoice, approved)."""
    from aap.db.models import Document
    from aap.pipeline.stages import latest_version
    from aap.submit.decision import approved_invoice

    approved = latest_version(session, document_id, ["approved"])
    if approved is not None:
        return CanonicalInvoice.model_validate(approved.canonical_invoice), True
    doc = session.get(Document, document_id)
    if doc is None:
        raise AploadError("document not found")
    inv, _, _ = approved_invoice(session, doc)
    return inv, False


def main(argv: list[str] | None = None) -> int:
    from aap.db.models import Document, InvoiceVersion
    from aap.db.session import session_scope

    p = argparse.ArgumentParser(prog="python -m aap.submit.apload", description=__doc__.split("\n\n")[0])
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--document", action="append", type=uuid.UUID, default=[],
                   help="documents to write (default: every document with an approved version)")
    p.add_argument("--source", default=None, help=f"InvoiceSource (default {get_settings().invoice_source})")
    args = p.parse_args(argv)
    with session_scope() as session:
        ids = args.document or list(session.scalars(
            select(InvoiceVersion.document_id).join(Document, Document.id == InvoiceVersion.document_id)
            .where(InvoiceVersion.kind == "approved").distinct()))
        invoices = []
        for doc_id in ids:
            inv, approved = document_invoice(session, doc_id)
            if not approved:
                print(f"{doc_id}: not approved yet, written as it would be approved now", file=sys.stderr)
            invoices.append(inv)
        text = apload_text(invoices, args.source)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8", newline="\n")
    print(f"{args.out}: {len(invoices)} invoice(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
