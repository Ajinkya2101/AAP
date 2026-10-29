"""APLOAD export against the functional team's sample (APILOAD_20251118.csv, second invoice)."""

from datetime import date
from decimal import Decimal

import pytest

from aap.schemas.canonical import CanonicalInvoice, CanonicalLine
from aap.schemas.fields import Field
from aap.submit.apload import AploadError, apload_text

SAMPLE = """\
H|InvoiceNumber|VendorNumber|SiteName|PONumber|ReleaseNumber|InvoiceDate|InvoiceAmount|InvoiceSource|TaxAmount|
L|InvoiceNumber|VendorNumber|SiteName|LineTypeLookupCode|POLineNumber|POShipmentNumber|InvoiceLineNumber|InvoicedQuantity|UnitPrice|BuyerItemNumber|Distribution|
H|0100531618-435-7|22662|Bruin Group|||11152025|28050.33000|BRUIN|1533.22000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||1|1|2589.37||04.02.90009.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||2|1|3649.96||04.01.90506.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||3|1|7522.59||04.01.90506.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||4|1|1774.21||04.01.90506.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||5|1|2879.45||04.06.00317.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||6|1|286.22||04.06.00091.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||7|1|429.33||04.06.00006.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||8|1|433.82||04.06.00237.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||9|1|286.22||04.06.90803.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||10|1|435.87||04.06.00140.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||11|1|429.33||04.06.00317.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||12|1|148.24||04.06.00321.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||13|1|140.41||04.06.00189.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||14|1|155.91||04.06.00080.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||15|1|168.33||04.06.00158.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||16|1|143.11||04.06.00071.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||17|1|292.72||04.06.00006.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||18|1|428.37||04.07.00301.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||19|1|2591.30||04.01.90506.68033.00000.000.00.000|
L|0100531618-435-7|22662|Bruin Group|ITEM|||20|1|1732.35||04.01.90506.68033.00000.000.00.000|
"""


def _invoice_from_sample() -> CanonicalInvoice:
    """The approved invoice behind the sample rows: amount-only lines, tax as a TAX line (stage 7)."""
    rows = [r.split("|") for r in SAMPLE.splitlines()[2:]]
    h = rows[0]
    inv = CanonicalInvoice()
    inv.invoice_num, inv.vendor_number, inv.vendor_site_code = Field(value=h[1]), Field(value=h[2]), Field(value=h[3])
    inv.invoice_date = Field(value=date(int(h[6][4:]), int(h[6][:2]), int(h[6][2:4])))
    inv.invoice_amount = Field(value=Decimal("28050.33"))
    inv.lines = [CanonicalLine(line_number=int(r[7]), amount=Field(value=Decimal(r[9])),
                               dist_account=Field(value=r[11])) for r in rows[1:]]
    inv.lines.append(CanonicalLine(line_number=21, line_type="TAX", printed=False,
                                   amount=Field(value=Decimal("1533.22"))))
    return inv


def test_apload_matches_sample():
    assert apload_text([_invoice_from_sample()], source="BRUIN") == SAMPLE


def test_lines_sum_to_amount_minus_tax():
    inv = _invoice_from_sample()
    lines = sum(ln.amount.value for ln in inv.lines if ln.line_type != "TAX")
    assert lines + Decimal("1533.22") == inv.invoice_amount.value


def test_quantity_and_price_kept_when_they_give_the_amount():
    inv = _invoice_from_sample()
    inv.lines[0].quantity, inv.lines[0].unit_price = Field(value=Decimal("16408")), Field(value=Decimal("0.05621"))
    inv.lines[0].amount = Field(value=Decimal("922.29"))
    row = apload_text([inv], source="X").splitlines()[3]
    assert row.split("|")[8:10] == ["16408", "0.05621"]


def test_po_matched_line_has_po_and_no_distribution():
    inv = _invoice_from_sample()
    inv.po_number = Field(value="206141")
    inv.lines[0].po_line_number = Field(value=1)
    rows = apload_text([inv], source="X").splitlines()
    assert rows[2].split("|")[4] == "206141"
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
