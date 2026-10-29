"""Follow-ups of the functional-team call (PLAN-REAL-INVOICES R1b): POs per line and blanket
releases, cost center from the location, the tax re-read, the store table loader. No database."""

from decimal import Decimal

import pytest

from aap.adapters.base import PoDetail, PoLine, PoRelease, PoShipment
from aap.agents.nodes.gl import with_cost_center
from aap.agents.nodes.matching import _allowed, ref_key
from aap.extraction.extract import PageInput, extract, tax_gap
from aap.llm.client import LlmResponse
from aap.mapping.locations import LocationTableError, location_key, read_table
from aap.mapping.map import _map_po_refs
from aap.normalize.normalize import _po_ref
from aap.schemas.canonical import CanonicalInvoice, CanonicalLine
from aap.schemas.fields import Field, Flag, Method
from aap.schemas.source import ExtractedInvoice
from aap.submit.decision import DecisionError, apply_corrections

D = Decimal


# ------------------------------------------------------------- PO references
@pytest.mark.parametrize(("printed", "po", "release"), [
    ("4500210-2", "4500210", 2),
    ("PO 4500210 Release 2", "4500210", 2),
    ("Rel. #3 / 4500210", "4500210", 3),
    ("Your PO# 4500123", "4500123", None),
    ("Delivery release 4500210", "4500210", None),     # no release number after the label
    ("PO-2024-0001", "2024", None),                    # a 4-digit suffix is not a release
])
def test_po_reference_splits_po_and_release(printed, po, release):
    p, r = _po_ref(Field(value=printed, source_value=printed))
    assert (p.value, r.value) == (po, release)


def _line(n: int, po: str | None = None, release: int | None = None) -> CanonicalLine:
    return CanonicalLine(line_number=n, description=Field(value=f"item {n}"), amount=Field(value=D("10")),
                         po_number=Field(value=po), po_release_num=Field(value=release))


def test_line_po_wins_and_other_lines_take_the_header_po():
    inv = CanonicalInvoice(po_number=Field(value="4500123"))
    inv.lines = [_line(1), _line(2, "4500999")]
    _map_po_refs(inv, inv.lines)
    assert [ln.po_number.value for ln in inv.lines] == ["4500123", "4500999"]
    assert inv.lines[0].po_number.method == Method.derived
    assert inv.po_refs() == [("4500123", None), ("4500999", None)]


def test_header_po_derived_only_when_every_line_names_the_same_one():
    inv = CanonicalInvoice()
    inv.lines = [_line(1, "4500210", 2), _line(2, "4500210", 2)]
    _map_po_refs(inv, inv.lines)
    assert (inv.po_number.value, inv.po_release_num.value) == ("4500210", 2)
    several = CanonicalInvoice()
    several.lines = [_line(1, "4500123"), _line(2, "4500124")]
    _map_po_refs(several, several.lines)
    assert several.po_number.value is None and several.po_refs() == [("4500123", None), ("4500124", None)]


def test_a_line_pairs_only_with_its_own_po():
    inv = CanonicalInvoice()
    inv.lines = [_line(1, "4500123"), _line(2, "4500210", 2), _line(3)]
    refs = [{"po": "4500123", "release": None, "printed_release": None, "ref": "4500123"},
            {"po": "4500210", "release": 2, "printed_release": 2, "ref": "4500210-2"}]
    allowed = _allowed(inv, refs)
    assert allowed == {1: {"4500123"}, 2: {"4500210-2"}, 3: {"4500123", "4500210-2"}}
    assert ref_key("4500210", 2) == "4500210-2" and ref_key("4500123", None) == "4500123"


def _ship(release: int | None, qty: str, received: str) -> PoShipment:
    return PoShipment(line_location_id=1, shipment_num=1, quantity=D(qty), quantity_received=D(received),
                      quantity_billed=D(0), quantity_cancelled=D(0), price_override=D("5"), receipt_required=True,
                      match_option="P", closed_code="OPEN", need_by_date=None, po_distribution_id=1, ccid=1,
                      ccid_concatenated="01-310-6210-0000", release_num=release, po_release_id=release)


def _blanket() -> PoDetail:
    lines = [PoLine(po_line_id=1, line_num=1, item_id=None, item_number=None, item_description="Gloves",
                    category_id=None, uom="Each", unit_price=D("5"), quantity=None, closed_code="OPEN",
                    cancelled=False, shipments=[_ship(1, "10", "10"), _ship(2, "4", "0")]),
             PoLine(po_line_id=2, line_num=2, item_id=None, item_number=None, item_description="Masks",
                    category_id=None, uom="Each", unit_price=D("2"), quantity=None, closed_code="OPEN",
                    cancelled=False, shipments=[_ship(1, "6", "6")])]
    return PoDetail(po_header_id=1, po_number="4500210", type="BLANKET", org_id=204, vendor_id=1, vendor_site_id=1,
                    currency_code="USD", authorization_status="APPROVED", closed_code="OPEN", cancelled=False,
                    lines=lines, releases=[PoRelease(po_release_id=1, release_num=1, authorization_status="APPROVED",
                                                     closed_code="CLOSED FOR INVOICE", cancelled=False),
                                           PoRelease(po_release_id=2, release_num=2, authorization_status="APPROVED",
                                                     closed_code="OPEN", cancelled=False)])


def test_blanket_agreement_seen_through_one_release():
    po = _blanket()
    rel2 = po.for_release(2)
    assert rel2.reference == "4500210-2" and [ln.line_num for ln in rel2.lines] == [1]
    assert (rel2.lines[0].ordered, rel2.lines[0].received) == (D(4), D(0))
    assert rel2.open_for_invoice and not po.for_release(1).open_for_invoice


# ----------------------------------------------------------------- corrections
def test_clerk_sets_a_line_po_with_its_release():
    inv = CanonicalInvoice(po_number=Field(value="4500123"))
    inv.lines = [_line(1), _line(2)]
    apply_corrections(inv, {"lines": {"1": {"po_number": "4500210-2", "po_line_number": 1},
                                      "2": {"po_line_number": 3}}}, "clerk")
    assert (inv.lines[0].po_number.value, inv.lines[0].po_release_num.value) == ("4500210", 2)
    assert (inv.lines[1].po_number.value, inv.lines[1].po_line_number.value) == ("4500123", 3)
    with pytest.raises(DecisionError, match="not a PO number"):
        apply_corrections(inv, {"lines": {"1": {"po_number": "4500210-x"}}}, "clerk")


# ------------------------------------------------------------ GL cost center
def test_location_cost_center_replaces_the_segment():
    assert with_cost_center("01-510-6420-0000", "316", 2, "-") == "01-316-6420-0000"
    assert with_cost_center("04.02.90009.68033.00000.000.00.000", "00316", 3, ".") == \
        "04.02.00316.68033.00000.000.00.000"
    assert with_cost_center("01-510", "316", 3, "-") is None


def test_store_table_reader():
    rows = read_table("Store;Cost Center\nSTORE 316;00316\n0368;00368\n\n316;00316\n")
    assert rows == [("STORE 316", "00316"), ("368", "00368"), ("316", "00316")]
    assert location_key("0368") == "368" and location_key("KRS 51") == "KRS 51"
    with pytest.raises(LocationTableError, match="no column"):
        read_table("name,account\nx,1\n")
    with pytest.raises(LocationTableError, match="twice"):
        read_table("location,cc\n316,1\n0316,2\n")


# ------------------------------------------------------------------ tax re-read
def _extracted(taxes: list[tuple[str, str]], **header) -> ExtractedInvoice:
    base = {k: None for k in ExtractedInvoice.model_fields if k not in ("lines", "taxes")}
    base.update(subtotal="1000.00", total="1149.75", **header)
    return ExtractedInvoice.model_validate({**base, "lines": [], "taxes": [
        {"label": label, "rate": None, "amount": amount} for label, amount in taxes]})


def test_tax_gap():
    assert tax_gap(_extracted([("GST", "50.00")])) == D("99.75")          # QST not read
    assert tax_gap(_extracted([("GST", "50.00"), ("QST", "99.75")])) is None
    assert tax_gap(_extracted([])) is None                                  # no tax read: nothing to compare
    assert tax_gap(_extracted([], tax_amount="149.75")) is None


class _Llm:
    def __init__(self, first: ExtractedInvoice, taxes: list[dict]):
        self.first, self.taxes, self.tasks = first, taxes, []

    def ask(self, req):
        self.tasks.append(req.task)
        data = self.first.model_dump() if req.task == "extract" else {"taxes": self.taxes}
        return LlmResponse(data=data, raw="", model="fake", backend="fake", duration_ms=1)


def _page() -> list[PageInput]:
    return [PageInput(page_no=1, kind="pdf", route="text_only", text="Sous-total 1000.00 TPS 50.00 TVQ 99.75",
                      words=[], image_png=None)]


def test_tax_rows_read_again_when_they_do_not_add_up():
    llm = _Llm(_extracted([("TPS", "50.00")]), [{"label": "TPS", "rate": "5%", "amount": "50.00"},
                                                 {"label": "TVQ", "rate": "9.975%", "amount": "99.75"}])
    out = extract(_page(), llm)
    assert llm.tasks == ["extract", "extract_taxes"] and out.requests == 2
    assert [t.label for t in out.extracted.taxes] == ["TPS", "TVQ"] and Flag.tax_reread in out.flags


def test_reread_that_does_not_close_the_gap_is_not_used():
    llm = _Llm(_extracted([("TPS", "50.00")]), [{"label": "TPS", "rate": None, "amount": "50.00"}])
    out = extract(_page(), llm)
    assert [t.label for t in out.extracted.taxes] == ["TPS"] and Flag.tax_reread not in out.flags


def test_no_reread_when_the_taxes_add_up():
    llm = _Llm(_extracted([("TPS", "50.00"), ("TVQ", "99.75")]), [])
    extract(_page(), llm)
    assert llm.tasks == ["extract"]


def test_tax_read_as_freight_is_read_again():                          # Énergir, second v5 run
    first = _extracted([("TPS", "50.00")], freight_amount="99,75 $")       # sums look right with QST as freight
    llm = _Llm(first, [{"label": "TPS", "rate": None, "amount": "50.00"},
                       {"label": "TVQ", "rate": None, "amount": "99.75"}])
    pages = [PageInput(page_no=1, kind="pdf", route="text_only", words=[], image_png=None,
                       text="Sous-total 1000.00\nTPS (n° 121411813) 50,00 $\nTVQ (n° 1010997280) 99,75 $\nTotal")]
    out = extract(pages, llm)
    assert llm.tasks == ["extract", "extract_taxes"] and Flag.tax_reread in out.flags
    assert out.extracted.freight_amount is None and [t.label for t in out.extracted.taxes] == ["TPS", "TVQ"]
    # real freight on its own row stays freight, and is not read again
    real = _extracted([("TPS", "50.00")], freight_amount="99.75")
    llm = _Llm(real, [])
    pages[0].text = "Sous-total 1000.00\nTPS 50.00\nFreight and handling 99.75\nTotal"
    extract(pages, llm)
    assert llm.tasks == ["extract"]
