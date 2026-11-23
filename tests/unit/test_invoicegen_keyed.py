"""Invoice files for keyed invoices (tools.invoicegen.keyed): what is printed, from made-up rows. The PDF
rendering itself needs WeasyPrint (app image) and is not run here."""

import random
from datetime import date
from decimal import Decimal

import pytest
from tools.invoicegen import keyed


@pytest.mark.parametrize("desc,place", [
    ("NEW CASTLE DE 00373", (None, "NEW CASTLE", "DE")),
    ("LOGAN UT - 1295", (None, "LOGAN", "UT")),
    ("MURFREESBORO, TN", (None, "MURFREESBORO", "TN")),
    ("WILSHIRE AVE SW, CONCORD, NC, USA", ("WILSHIRE AVE SW", "CONCORD", "NC")),
    ("BATON ROUGE LA 70805", (None, "BATON ROUGE", "LA")),
    ("MIX SUPPLIER", None),
    ("COMPANY SHOP GENERAL", None),
])
def test_places_from_cost_center_descriptions(desc, place):
    assert keyed.parse_place(desc) == place


def test_items_sum_to_the_line_and_follow_the_profile():
    entries = [{"desc": ["A", "B", "C"], "pick": [2, 3], "rest": True, "qty": [1, 9], "code": 4},
               {"desc": "FREIGHT", "fixed": "9.95"}, {"desc": "Fuel", "share": [0.1, 0.2]}]
    for seed in range(40):
        amount = Decimal(random.Random(seed).randint(2000, 90000)) / 100
        items = keyed.split_amount(amount, entries, random.Random(seed))
        assert sum(i["amount"] for i in items) == amount
        names = [i["description"] for i in items]
        assert ("FREIGHT" in names) == (Decimal("9.95") * 2 < amount)       # a fixed charge never takes half
        assert all(i["code"] and len(i["code"]) == 4 for i in items if i["description"] in "ABC")


def _sources():
    s = keyed.Sources()
    s.combos = {1: {"CODE_COMBINATION_ID": 1, **{f"SEGMENT{i}": v for i, v in enumerate(
                    ["10", "20", "00110", "61210", "00000", "000", "00", "000"], start=1)}},
                2: {"CODE_COMBINATION_ID": 2, **{f"SEGMENT{i}": v for i, v in enumerate(
                    ["10", "20", "90002", "65020", "PJ001", "000", "00", "000"], start=1)}}}
    s.flex = {(3, "00110"): "SPRINGFIELD IL 00110", (3, "90002"): "MIX SUPPLIER", (5, "PJ001"): "PJ_EXAMPLE"}
    return s


def _invoice(ccid, total="300.00", line="300.00"):
    return keyed.Keyed(
        header={"INVOICE_ID": 7, "INVOICE_NUM": "A-100", "INVOICE_DATE": date(2026, 9, 20), "INVOICE_AMOUNT": total,
                "INVOICE_CURRENCY_CODE": "USD", "VENDOR_ID": 5, "VENDOR_SITE_ID": 6},
        lines=[{"LINE_NUMBER": 1, "LINE_TYPE_LOOKUP_CODE": "ITEM", "AMOUNT": line},
               {"LINE_NUMBER": 2, "LINE_TYPE_LOOKUP_CODE": "TAX", "AMOUNT": "0"}],
        dists=[{"INVOICE_LINE_NUMBER": 1, "AMOUNT": line, "DIST_CODE_COMBINATION_ID": ccid}])


def test_a_store_cost_center_prints_as_the_store():
    p = keyed.printed_values(_sources(), _invoice(1), {"items": [{"desc": "Freight", "rest": True}], "due_days": 15},
                             keyed.Fake("x"))
    loc = p["location"]
    assert (loc["kind"], loc["store"], loc["city"], loc["state"]) == ("store", "110", "Springfield", "IL")
    assert loc["name"] == "Store #110" and loc["zip"][:3] in {str(n) for n in range(600, 630)}
    assert p["due_date"] == date(2026, 10, 5) and p["tax"] is None and p["total"] == Decimal("300.00")
    assert [i["amount"] for i in p["lines"]] == [Decimal("300.00")]


def test_another_cost_center_prints_as_the_profile_party_and_the_project_as_po():
    profile = {"items": [{"desc": "Freight", "rest": True}], "po": "project",
               "parties": {"90002": {"name": "Mix Supplier Inc", "street": "1 Mill Rd", "city": "Greensboro",
                                     "state": "NC", "zip": "27409"}}}
    p = keyed.printed_values(_sources(), _invoice(2), profile, keyed.Fake("x"))
    assert p["location"]["kind"] == "party" and p["location"]["name"] == "Mix Supplier Inc"
    assert p["po_number"] == "PJ001"


def test_tax_above_the_lines_is_printed_and_the_truth_carries_the_printed_values():
    p = keyed.printed_values(_sources(), _invoice(1, total="321.00", line="300.00"),
                             {"items": [{"desc": "Freight", "rest": True}]}, keyed.Fake("x"))
    assert p["tax"] == Decimal("21.00") and p["subtotal"] == Decimal("300.00")
    src = keyed.source_truth(p, "Example Freight LLC")
    assert src["total"] == "321.00" and src["tax_amount"] == "21.00" and src["invoice_number"] == "A-100"
    assert src["ship_to"].startswith("Store #110")
    exp = keyed.expected_values(_sources(), _invoice(1))
    assert exp["lines"][0]["distributions"][0]["account"] == "10.20.00110.61210.00000.000.00.000"


def test_decorations_are_seeded_by_the_invoice():
    assert keyed.Fake("invoice:1").digits(8) == keyed.Fake("invoice:1").digits(8)
    assert keyed.Fake("invoice:1").digits(8) != keyed.Fake("invoice:2").digits(8)
