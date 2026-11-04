"""Payables lookups (P3.3): code resolution, the list-of-values workbook, the replica loader, rule V-LOV.
Made-up values."""

from datetime import date
from decimal import Decimal

import openpyxl

from aap.clientdata.lov import convert, main
from aap.clientdata.readers import read_delivery
from aap.common.lookups import resolve_code
from aap.db.models import RefLookup
from aap.ebs_mock.client_load import Replica, lookup_rows
from aap.mapping.reference import Reference
from aap.schemas.canonical import CanonicalInvoice, CanonicalLine
from aap.schemas.fields import Field
from aap.validation.engine import RuleContext
from aap.validation.rules.r14_lookups import lookup_codes


def test_resolve_codes_meanings_and_aliases():
    assert resolve_code("INVOICE TYPE", "CREDIT MEMO") == ("CREDIT", True)
    assert resolve_code("INVOICE TYPE", "Standard") == ("STANDARD", True)
    assert resolve_code("INVOICE TYPE", "Withholding Tax") == ("AWT", True)
    assert resolve_code("INVOICE LINE TYPE", "Freight") == ("FREIGHT", True)
    assert resolve_code("INVOICE LINE TYPE", "Prepayment") == ("PREPAY", True)      # per lookup type
    assert resolve_code("INVOICE TYPE", "Prepayment") == ("PREPAYMENT", True)
    assert resolve_code("INVOICE TYPE", "Store  rebate") == ("STORE REBATE", False)


def _sections(path):
    """Vikram-style: a title row, a header row, the values; blocks separated by empty rows."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "LOVs"
    for r in [["LOVs"], [], ["Invoice Type"], [None, None, "Invoice Type", "Description"],
              [None, None, "STANDARD", "Standard invoice"], [None, None, "CREDIT MEMO", "Credit Memo"],
              [None, None, "STORE REBATE", "Made-up type"], [], ["Line Type"], [None, None, "Line Type", "Description"],
              [None, None, "Item", "Goods"], [None, None, "Freight", "Shipping"], [], ["Match Action"], ["Invoice"],
              ["Receipt"], [], ["Terms"], [None, "Terms", "Description"], [None, "30 NET", "Net 30"]]:
        ws.append(r)
    wb.save(path)


def _columns(path):
    """Siva's instruction: a header tab and a line tab, field names on top, eligible values below."""
    wb = openpyxl.Workbook()
    h = wb.active
    h.title = "Header"
    for r in [["Type", "Source", "Payment Method"], ["Standard", "AAP", "EFT"], ["Credit Memo", "Manual Invoice Entry", None]]:
        h.append(r)
    ln = wb.create_sheet("Line")
    for r in [["Type", "Description"], ["Item", None], ["Miscellaneous", None], ["Tax", None]]:
        ln.append(r)
    wb.save(path)


def test_sections_layout(tmp_path):
    _sections(tmp_path / "lov.xlsx")
    r = convert(tmp_path / "lov.xlsx")
    codes = {(x["LOOKUP_TYPE"], x["LOOKUP_CODE"]): x for x in r.rows}
    assert set(codes) == {("INVOICE TYPE", "STANDARD"), ("INVOICE TYPE", "CREDIT"), ("INVOICE TYPE", "STORE REBATE"),
                          ("INVOICE LINE TYPE", "ITEM"), ("INVOICE LINE TYPE", "FREIGHT")}
    assert codes[("INVOICE TYPE", "CREDIT")]["MEANING"] == "Credit Memo"
    assert codes[("INVOICE TYPE", "STORE REBATE")]["CODE_CONFIRMED"] == "N"
    assert r.unconfirmed == ["INVOICE TYPE: 'STORE REBATE' -> STORE REBATE"]
    assert any(o.startswith("LOVs!Match Action (2") for o in r.other_lists)
    assert any(o.startswith("LOVs!Terms (1") for o in r.other_lists)


def test_columns_layout_and_csv_read_as_a_delivery_table(tmp_path):
    _columns(tmp_path / "eligible values.xlsx")
    assert main([str(tmp_path / "eligible values.xlsx")]) == 0
    d = read_delivery(tmp_path)
    rows = {(x["LOOKUP_TYPE"], x["LOOKUP_CODE"]) for x in d.rows("FND_LOOKUP_VALUES")}
    assert rows == {("INVOICE TYPE", "STANDARD"), ("INVOICE TYPE", "CREDIT"), ("SOURCE", "AAP"),
                    ("SOURCE", "MANUAL INVOICE ENTRY"), ("INVOICE LINE TYPE", "ITEM"),
                    ("INVOICE LINE TYPE", "MISCELLANEOUS"), ("INVOICE LINE TYPE", "TAX")}
    assert any("eligible values.xlsx" in f for f in d.unknown_files)     # the workbook itself: listed, not read


def test_delivered_lookup_types_replace_the_seeded_ones(tmp_path):
    (tmp_path / "fnd_lookup_values.csv").write_text(
        "LOOKUP_TYPE,LOOKUP_CODE,MEANING,ENABLED_FLAG\nINVOICE TYPE,STANDARD,Standard,Y\nINVOICE TYPE,DEBIT,Debit,N\n",
        encoding="utf-8")
    r = Replica()
    r.rows["fnd.fnd_lookup_values"] = [
        {"lookup_type": "INVOICE TYPE", "lookup_code": c, "meaning": c} for c in ("STANDARD", "CREDIT", "MIXED")] + [
        {"lookup_type": "HOLD CODE", "lookup_code": "PRICE", "meaning": "Price"}]
    notes = lookup_rows(r, read_delivery(tmp_path))
    got = {(x["lookup_type"], x["lookup_code"], x.get("enabled_flag")) for x in r.rows["fnd.fnd_lookup_values"]}
    assert got == {("HOLD CODE", "PRICE", None), ("INVOICE TYPE", "STANDARD", "Y"), ("INVOICE TYPE", "DEBIT", "N")}
    assert notes == ["lookups from the delivery: INVOICE TYPE (2)"]


def _ref(**lists):
    ref = Reference(operating_units={}, suppliers={}, sites={}, sites_by_vendor={}, banks_by_vendor={}, terms={},
                    currencies={}, uoms={}, categories={}, tax_rates=[], tolerances={}, periods=[])
    for t, codes in lists.items():
        ref.lookups[t.replace("_", " ")] = {c: RefLookup(lookup_type=t, lookup_code=c, enabled=e) for c, e in codes}
    return ref


def _ctx(ref, invoice_type="STANDARD", line_types=("ITEM",), tax=None):
    inv = CanonicalInvoice()
    inv.invoice_type = Field(value=invoice_type)
    inv.invoice_amount = Field(value=Decimal("110.00"))
    inv.tax_amount = Field(value=tax)
    inv.lines = [CanonicalLine(line_number=i, line_type=t, amount=Field(value=Decimal("100.00")))
                 for i, t in enumerate(line_types, start=1)]
    return RuleContext(inv=inv, session=None, ref=ref, adapter=None, document=None, today=date(2026, 11, 4))


LISTS = dict(INVOICE_TYPE=[("STANDARD", True), ("CREDIT", True), ("DEBIT", False)],
             INVOICE_LINE_TYPE=[("ITEM", True), ("TAX", True)], SOURCE=[("AAP", True)])


def test_v_lov_passes_on_known_codes_and_checks_the_header_tax_line():
    [r] = lookup_codes(_ctx(_ref(**LISTS), tax=Decimal("10.00")))
    assert r.result == "pass" and "line 2 type TAX" in r.message


def test_v_lov_fails_unknown_or_disabled_codes():
    [r] = lookup_codes(_ctx(_ref(**LISTS), invoice_type="DEBIT", line_types=("ITEM", "FREIGHT")))
    assert r.result == "fail"
    assert "invoice type 'DEBIT' is disabled" in r.message and "line 2 type 'FREIGHT' is not in" in r.message
    assert r.field_paths == ["invoice_type", "lines[2].line_type"]
    [r] = lookup_codes(_ctx(_ref(**{**LISTS, "SOURCE": [("MANUAL INVOICE ENTRY", True)]})))
    assert r.result == "fail" and "invoice source 'AAP'" in r.message and r.field_paths == ["source"]


def test_v_lov_skips_types_without_a_list():
    [r] = lookup_codes(_ctx(_ref()))
    assert r.result == "skip"
    [r] = lookup_codes(_ctx(_ref(INVOICE_TYPE=[("STANDARD", True)])))
    assert r.result == "pass" and "not checked, no list: INVOICE LINE TYPE, SOURCE" in r.message
