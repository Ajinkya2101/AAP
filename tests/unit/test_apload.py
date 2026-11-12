"""APLOAD export in the functional team's sample layout (APILOAD file). The rows are made up: the
layout is the client's, the supplier, amounts and accounts are not."""

from datetime import date
from decimal import Decimal

import pytest

from aap.schemas.canonical import CanonicalInvoice, CanonicalLine
from aap.schemas.fields import Field
from aap.submit.apload import AploadError, apload_text

SAMPLE = """\
H|InvoiceNumber|VendorNumber|SiteName|PONumber|ReleaseNumber|InvoiceDate|InvoiceAmount|InvoiceSource|TaxAmount|
L|InvoiceNumber|VendorNumber|SiteName|LineTypeLookupCode|POLineNumber|POShipmentNumber|InvoiceLineNumber|InvoicedQuantity|UnitPrice|BuyerItemNumber|Distribution|
H|EX-40217-B|90001|Example Supply Co|||10072026|8494.12000|AAPTEST|518.42000|
L|EX-40217-B|90001|Example Supply Co|ITEM|||1|1|812.40||10.20.00110.61210.00000.000.00.000|
L|EX-40217-B|90001|Example Supply Co|ITEM|||2|1|1045.75||10.20.00125.61210.00000.000.00.000|
L|EX-40217-B|90001|Example Supply Co|ITEM|||3|1|233.10||10.30.00210.61400.00000.000.00.000|
L|EX-40217-B|90001|Example Supply Co|ITEM|||4|1|4410.00||10.20.00110.61210.00000.000.00.000|
L|EX-40217-B|90001|Example Supply Co|ITEM|||5|1|96.85||10.30.00215.61400.00000.000.00.000|
L|EX-40217-B|90001|Example Supply Co|ITEM|||6|1|1377.60||10.20.00140.61210.00000.000.00.000|
"""


def _invoice_from_sample() -> CanonicalInvoice:
    """The approved invoice behind the sample rows: amount-only lines, tax as a TAX line (stage 7)."""
    rows = [r.split("|") for r in SAMPLE.splitlines()[2:]]
    h = rows[0]
    inv = CanonicalInvoice()
    inv.invoice_num, inv.vendor_number, inv.vendor_site_code = Field(value=h[1]), Field(value=h[2]), Field(value=h[3])
    inv.invoice_date = Field(value=date(int(h[6][4:]), int(h[6][:2]), int(h[6][2:4])))
    inv.invoice_amount = Field(value=Decimal("8494.12"))
    inv.lines = [CanonicalLine(line_number=int(r[7]), amount=Field(value=Decimal(r[9])),
                               dist_account=Field(value=r[11])) for r in rows[1:]]
    inv.lines.append(CanonicalLine(line_number=7, line_type="TAX", printed=False,
                                   amount=Field(value=Decimal("518.42"))))
    return inv


def test_apload_matches_sample():
    assert apload_text([_invoice_from_sample()], source="AAPTEST") == SAMPLE


def test_lines_sum_to_amount_minus_tax():
    inv = _invoice_from_sample()
    lines = sum(ln.amount.value for ln in inv.lines if ln.line_type != "TAX")
    assert lines + Decimal("518.42") == inv.invoice_amount.value


def test_quantity_and_price_kept_when_they_give_the_amount():
    inv = _invoice_from_sample()
    inv.lines[0].quantity, inv.lines[0].unit_price = Field(value=Decimal("16408")), Field(value=Decimal("0.05621"))
    inv.lines[0].amount = Field(value=Decimal("922.29"))
    row = apload_text([inv], source="X").splitlines()[3]
    assert row.split("|")[8:10] == ["16408", "0.05621"]


def test_po_matched_line_has_po_and_no_distribution():
    inv = _invoice_from_sample()
    inv.po_number = Field(value="700123")
    inv.lines[0].po_line_number = Field(value=1)
    rows = apload_text([inv], source="X").splitlines()
    assert rows[2].split("|")[4] == "700123"
    assert rows[3].split("|")[5] == "1" and rows[3].split("|")[12] == ""
    assert rows[4].split("|")[4] == "ITEM" and rows[4].split("|")[5] == ""


def test_missing_supplier_number_is_an_error():
    inv = _invoice_from_sample()
    inv.vendor_number = Field()
    with pytest.raises(AploadError, match="supplier number"):
        apload_text([inv])


def test_blanket_release_goes_into_release_number():
    inv = _invoice_from_sample()
    inv.po_number, inv.po_release_num = Field(value="4500210"), Field(value=2)
    inv.lines[0].po_number, inv.lines[0].po_release_num = Field(value="4500210"), Field(value=2)
    inv.lines[0].po_line_number = Field(value=1)
    h = apload_text([inv], source="X").splitlines()[2].split("|")
    assert (h[4], h[5]) == ("4500210", "2")


def test_lines_on_several_pos_are_refused():
    inv = _invoice_from_sample()
    for ln, po in zip(inv.lines[:2], ("4500123", "4500124"), strict=True):
        ln.po_number, ln.po_line_number = Field(value=po), Field(value=1)
    with pytest.raises(AploadError, match="2 POs"):
        apload_text([inv])
